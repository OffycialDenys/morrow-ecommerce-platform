from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from catalog.models import Product

from .forms import AddToCartForm, ChangeCartQuantityForm
from .services import CartError, add_variant, cart_summary, remove_variant, set_variant_quantity


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
