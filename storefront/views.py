from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from accounts.models import SavedProduct
from catalog.forms import search_products
from catalog.models import Category, Product, ProductVariant
from promotions.points import get_points_balance


def home(request):
    categories = Category.objects.filter(is_active=True)
    form, products = search_products({"sort": "newest"})
    return render(
        request,
        "storefront/home.html",
        {"categories": categories, "products": products[:4], "search_form": form},
    )


def shop(request):
    form, products = search_products(request.GET)
    page = None
    previous_page_url = None
    next_page_url = None
    if form.is_valid():
        page = Paginator(products, 12).get_page(form.cleaned_data.get("page") or 1)
        if page.has_previous():
            query = request.GET.copy()
            query["page"] = page.previous_page_number()
            previous_page_url = f"?{query.urlencode()}"
        if page.has_next():
            query = request.GET.copy()
            query["page"] = page.next_page_number()
            next_page_url = f"?{query.urlencode()}"
    currencies = (
        Product.objects.filter(publication_status=Product.PublicationStatus.PUBLISHED)
        .order_by()
        .values_list("currency", flat=True)
        .distinct()
    )
    return render(
        request,
        "storefront/product_list.html",
        {
            "form": form,
            "page": page,
            "previous_page_url": previous_page_url,
            "next_page_url": next_page_url,
            "categories": Category.objects.filter(is_active=True),
            "currencies": currencies,
        },
    )


def product_detail(request, slug: str):
    product = get_object_or_404(
        Product.objects.filter(publication_status=Product.PublicationStatus.PUBLISHED)
        .select_related("category")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(is_active=True).order_by(
                    "price_minor", "sku"
                ),
            )
        ),
        slug=slug,
    )
    variants = list(product.variants.all())
    saved = (
        request.user.is_authenticated
        and SavedProduct.objects.filter(user=request.user, product=product).exists()
    )
    return render(
        request,
        "storefront/product_detail.html",
        {
            "product": product,
            "variants": variants,
            "is_saved": saved,
            "has_available_variant": any(variant.can_be_added_to_cart() for variant in variants),
        },
    )


@login_required
def account(request):
    return render(
        request,
        "storefront/account.html",
        {
            "saved_count": SavedProduct.objects.filter(user=request.user).count(),
            "order_count": request.user.orders.count(),
            "points_enabled": settings.POINTS_PROGRAM_ENABLED,
            "points_balance": get_points_balance(request.user),
        },
    )
