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
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalog.models import Product

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
