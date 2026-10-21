from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from commerce.models import OrderLine

from .forms import ProductReviewForm
from .models import ProductReview
from .services import ReviewError, ReviewRateLimited, is_review_eligible, submit_review


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
    existing = ProductReview.objects.filter(
        order_line=line, customer=request.user
    ).first()
    if existing is not None and existing.status == ProductReview.Status.HIDDEN:
        raise Http404
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
            return redirect("accounts:order-detail", order_number=review.order_line.order.order_number)
    return render(
        request,
        "reviews/submit.html",
        {"form": form, "order_line": line, "existing_review": existing},
    )
