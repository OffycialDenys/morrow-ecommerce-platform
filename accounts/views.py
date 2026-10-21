from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from catalog.models import Product
from commerce.models import Order
from promotions.models import PointEntry
from promotions.points import get_points_balance
from reviews.models import ProductReview, ReviewAppeal
from sitecore.audit import record_audit_event
from sitecore.models import AuditEvent

from .exports import stream_account_export
from .forms import RegistrationForm
from .models import SavedProduct


class EmailLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class EmailLogoutView(LogoutView):
    next_page = reverse_lazy("storefront:home")


class StorePasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class StorePasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class StorePasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class StorePasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


def register(request):
    if request.user.is_authenticated:
        return redirect("storefront:account")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Your account is ready.")
        return redirect("storefront:account")
    return render(request, "accounts/register.html", {"form": form})


@require_POST
def toggle_saved_product(request, product_id: int):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path(), reverse("accounts:login"))

    product = get_object_or_404(
        Product, pk=product_id, publication_status=Product.PublicationStatus.PUBLISHED
    )
    saved, created = SavedProduct.objects.get_or_create(user=request.user, product=product)
    if created:
        messages.success(request, "Saved for later.")
    else:
        saved.delete()
        messages.info(request, "Removed from your saved items.")

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("storefront:shop")


@never_cache
@login_required
def saved_products(request):
    from django.db.models import Min, Q

    products = (
        Product.objects.filter(saved_by__user=request.user)
        .annotate(min_price=Min("variants__price_minor", filter=Q(variants__is_active=True)))
        .select_related("category")
        .prefetch_related("variants")
    )
    return render(request, "storefront/saved_products.html", {"products": products})


@never_cache
@login_required
def order_history(request):
    orders = Order.objects.filter(user=request.user).prefetch_related("lines")
    return render(request, "accounts/orders.html", {"orders": orders})


@never_cache
@login_required
def order_detail(request, order_number):
    order = get_object_or_404(
        Order.objects.prefetch_related("lines"),
        order_number=order_number,
        user=request.user,
    )
    points_total = PointEntry.objects.filter(order=order).aggregate(total=Sum("points"))["total"]
    lines = list(order.lines.all())
    reviews_by_line = {
        review.order_line_id: review
        for review in ProductReview.objects.filter(order_line__in=lines, customer=request.user)
    }
    review_ids = [review.pk for review in reviews_by_line.values()]
    appeals_by_review = {
        appeal.review_id: appeal for appeal in ReviewAppeal.objects.filter(review_id__in=review_ids)
    }
    review_eligible = (
        order.payment_status == Order.PaymentStatus.PAID
        and order.fulfillment_status == Order.FulfillmentStatus.DELIVERED
    )
    for line in lines:
        line.customer_review = reviews_by_line.get(line.pk)
        line.customer_appeal = (
            appeals_by_review.get(line.customer_review.pk)
            if line.customer_review is not None
            else None
        )
        line.review_eligible = review_eligible and line.variant_id is not None
    return render(
        request,
        "accounts/order_detail.html",
        {"order": order, "points_total": points_total, "lines": lines},
    )


@never_cache
@login_required
def points_history(request):
    entries = PointEntry.objects.filter(customer=request.user).select_related(
        "order", "order_line", "reversal_of"
    )
    page = Paginator(entries, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "accounts/points.html",
        {
            "balance": get_points_balance(request.user),
            "page": page,
        },
    )


@never_cache
@login_required
@require_GET
def download_account_data(request):
    record_audit_event(
        action=AuditEvent.Action.ACCOUNT_DATA_EXPORTED,
        request_id=request.request_id,
        actor=request.user,
        subject=request.user,
    )
    response = StreamingHttpResponse(
        stream_account_export(request.user.pk),
        content_type="application/json; charset=utf-8",
    )
    response[
        "Content-Disposition"
    ] = f'attachment; filename="morrow-account-data-{timezone.localdate().isoformat()}.json"'
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Referrer-Policy"] = "no-referrer"
    return response
