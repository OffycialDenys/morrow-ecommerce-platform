import logging

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from catalog.models import Product

from .forms import AddToCartForm, ChangeCartQuantityForm
from .payments import PaymentError, process_verified_payment_event
from .providers import (
    MAX_WEBHOOK_BODY_BYTES,
    PayPalGateway,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderVerificationError,
    StripeGateway,
)
from .services import CartError, add_variant, cart_summary, remove_variant, set_variant_quantity

logger = logging.getLogger(__name__)


def cart(request):
    return render(request, "commerce/cart.html", {"cart": cart_summary(request.session)})


@require_POST
def add(request):
    form = AddToCartForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid option and quantity.")
        return redirect("storefront:shop")
    try:
        add_variant(request.session, form.cleaned_data["variant_id"], form.cleaned_data["quantity"])
    except CartError as error:
        messages.error(request, str(error))
        product = Product.objects.filter(
            variants__pk=form.cleaned_data["variant_id"],
            publication_status=Product.PublicationStatus.PUBLISHED,
        ).first()
        if product:
            return redirect("storefront:product-detail", slug=product.slug)
        return redirect("storefront:shop")
    messages.success(request, "Added to your bag.")
    return redirect("commerce:cart")


@require_POST
def update(request, variant_id: int):
    form = ChangeCartQuantityForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a quantity between one and ten.")
        return redirect("commerce:cart")
    try:
        set_variant_quantity(request.session, variant_id, form.cleaned_data["quantity"])
    except CartError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Your bag was updated.")
    return redirect("commerce:cart")


@require_POST
def remove(request, variant_id: int):
    remove_variant(request.session, variant_id)
    messages.info(request, "Item removed from your bag.")
    return redirect("commerce:cart")


def checkout(request):
    return render(
        request, "commerce/checkout_unavailable.html", {"cart": cart_summary(request.session)}
    )


def _webhook_error_response(error: Exception) -> HttpResponse:
    if isinstance(error, ProviderVerificationError):
        return JsonResponse({"received": False}, status=400)
    if isinstance(error, (ProviderConfigurationError, ProviderRequestError)):
        return JsonResponse({"received": False}, status=503)
    if isinstance(error, PaymentError):
        return JsonResponse({"received": False}, status=400)
    return JsonResponse({"received": False}, status=500)


def _request_body_within_limit(request) -> bool:
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return False
    if content_length > MAX_WEBHOOK_BODY_BYTES:
        return False
    return len(request.body) <= MAX_WEBHOOK_BODY_BYTES


@csrf_exempt
@require_POST
def stripe_webhook(request):
    if not _request_body_within_limit(request):
        return JsonResponse({"received": False}, status=413)
    try:
        event = StripeGateway().verify_webhook(
            request.body, request.headers.get("Stripe-Signature")
        )
        if event is not None:
            process_verified_payment_event(event)
    except (
        ProviderConfigurationError,
        ProviderRequestError,
        ProviderVerificationError,
        PaymentError,
    ) as error:
        return _webhook_error_response(error)
    except Exception:
        logger.exception("Stripe webhook processing failed")
        return JsonResponse({"received": False}, status=500)
    return JsonResponse({"received": True})


@csrf_exempt
@require_POST
def paypal_webhook(request):
    if not _request_body_within_limit(request):
        return JsonResponse({"received": False}, status=413)
    try:
        gateway = PayPalGateway()
        provider_event = gateway.verify_webhook(request.body, request.headers)
        event = gateway.normalize_webhook(provider_event, request.body)
        if event is not None:
            process_verified_payment_event(event)
    except (
        ProviderConfigurationError,
        ProviderRequestError,
        ProviderVerificationError,
        PaymentError,
    ) as error:
        return _webhook_error_response(error)
    except Exception:
        logger.exception("PayPal webhook processing failed")
        return JsonResponse({"received": False}, status=500)
    return JsonResponse({"received": True})
