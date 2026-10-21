from django.core.paginator import Paginator
from django.db.models import Min, Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .forms import search_products
from .models import Product, ProductVariant


def _error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _variant_data(variant: ProductVariant) -> dict:
    return {
        "id": variant.pk,
        "sku": variant.sku,
        "label": variant.label,
        "attributes": variant.attributes,
        "priceMinor": variant.price_minor,
        "currency": variant.product.currency,
        "available": variant.can_be_added_to_cart(),
        "availableUnits": variant.available_units,
    }


def _product_data(product: Product, include_variants: bool = False) -> dict:
    data = {
        "id": product.pk,
        "name": product.name,
        "slug": product.slug,
        "category": product.category.name if product.category_id else None,
        "description": product.description,
        "imageUrl": product.image_url or None,
        "currency": product.currency,
        "minimumPriceMinor": product.min_price,
    }
    if include_variants:
        data["variants"] = [
            _variant_data(variant)
            for variant in product.variants.filter(is_active=True).order_by("price_minor", "sku")
        ]
    return data


@require_GET
def product_list(request):
    form, products = search_products(request.GET)
    if not form.is_valid():
        return _error("VALIDATION_ERROR", "One or more search filters are invalid.", 400)

    products = products.annotate(
        min_price=Min("variants__price_minor", filter=Q(variants__is_active=True))
    )
    page_number = form.cleaned_data.get("page") or 1
    page = Paginator(products, 24).get_page(page_number)
    return JsonResponse(
        {
            "count": page.paginator.count,
            "page": page.number,
            "pageSize": page.paginator.per_page,
            "pageCount": page.paginator.num_pages,
            "results": [_product_data(product) for product in page.object_list],
        }
    )


@require_GET
def product_detail(request, slug: str):
    product = (
        Product.objects.filter(publication_status=Product.PublicationStatus.PUBLISHED)
        .annotate(min_price=Min("variants__price_minor", filter=Q(variants__is_active=True)))
        .filter(slug=slug, min_price__isnull=False)
        .select_related("category")
        .first()
    )
    if product is None:
        return _error("NOT_FOUND", "Product not found.", 404)
    return JsonResponse(_product_data(product, include_variants=True))
