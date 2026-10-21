from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from commerce.models import OrderLine

from .forms import ProductReviewForm, ReviewAppealForm, ReviewReportForm
from .models import ProductReview, ReviewAppeal, ReviewReport
from .services import (
    ReviewError,
    ReviewRateLimited,
    appeal_review,
    is_review_eligible,
    report_review,
    submit_review,
)


@login_required
@require_http_methods(["GET", "POST"])
def submit(request, order_line_id: int):
    line = get_object_or_404(
        OrderLine.objects.select_related("order", "variant__product"),
        pk=order_line_id,
        order__user=request.user,
    )
    if not is_review_eligible(line, request.user):
        raise Http404
    existing = ProductReview.objects.filter(order_line=line, customer=request.user).first()
    if existing is not None and existing.status == ProductReview.Status.HIDDEN:
        raise Http404
    if (
        existing is not None
        and existing.status == ProductReview.Status.REJECTED
        and ReviewAppeal.objects.filter(
            review=existing, status=ReviewAppeal.Status.PENDING
        ).exists()
    ):
        messages.info(request, "Wait for the pending appeal before editing this review.")
        return redirect("accounts:order-detail", order_number=line.order.order_number)
    form = ProductReviewForm(
        request.POST or None,
        initial={"rating": existing.rating, "body": existing.body} if existing else None,
    )
    if request.method == "POST" and form.is_valid():
        try:
            review = submit_review(
                customer=request.user,
                order_line_id=line.pk,
                rating=form.cleaned_data["rating"],
                body=form.cleaned_data["body"],
            )
        except ReviewError as error:
            form.add_error(None, str(error))
            if isinstance(error, ReviewRateLimited):
                return render(
                    request,
                    "reviews/submit.html",
                    {"form": form, "order_line": line, "existing_review": existing},
                    status=429,
                )
        else:
            messages.success(request, "Your review was submitted for moderation.")
            return redirect(
                "accounts:order-detail", order_number=review.order_line.order.order_number
            )
    return render(
        request,
        "reviews/submit.html",
        {"form": form, "order_line": line, "existing_review": existing},
    )


@login_required
@require_http_methods(["GET", "POST"])
def report(request, review_id: int):
    review = get_object_or_404(
        ProductReview.objects.select_related("product"),
        pk=review_id,
        status=ProductReview.Status.APPROVED,
    )
    if review.customer_id == request.user.pk:
        raise Http404
    if ReviewReport.objects.filter(review=review, reporter=request.user).exists():
        messages.info(request, "You have already reported this review.")
        return redirect("storefront:product-detail", slug=review.product.slug)
    form = ReviewReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            report_review(
                request.user,
                review_id=review.pk,
                category=form.cleaned_data["category"],
                details=form.cleaned_data["details"],
            )
        except ReviewError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Thank you. The review was sent to the store team.")
            return redirect("storefront:product-detail", slug=review.product.slug)
    return render(request, "reviews/report.html", {"form": form, "review": review})


@login_required
@require_http_methods(["GET", "POST"])
def appeal(request, review_id: int):
    review = get_object_or_404(
        ProductReview.objects.select_related("order_line__order"),
        pk=review_id,
        customer=request.user,
        status__in=(ProductReview.Status.REJECTED, ProductReview.Status.HIDDEN),
    )
    existing = ReviewAppeal.objects.filter(review=review).first()
    if existing is not None:
        messages.info(request, f"Your appeal is {existing.get_status_display().lower()}.")
        return redirect("accounts:order-detail", order_number=review.order_line.order.order_number)

    form = ReviewAppealForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            appeal_review(
                request.user,
                review_id=review.pk,
                reason=form.cleaned_data["reason"],
            )
        except ReviewError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "Your appeal was sent to the store team.")
            return redirect(
                "accounts:order-detail", order_number=review.order_line.order.order_number
            )
    return render(request, "reviews/appeal.html", {"form": form, "review": review})
