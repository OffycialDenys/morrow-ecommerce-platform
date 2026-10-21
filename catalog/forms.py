from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Min, Q

from commerce.money import major_to_minor

from .models import Product, validate_iso_currency


class ProductSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    category = forms.SlugField(required=False, max_length=100)
    currency = forms.CharField(required=False, max_length=3)
    minPrice = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=4)
    maxPrice = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=4)
    sort = forms.ChoiceField(
        required=False,
        choices=(
            ("relevance", "Recommended"),
            ("price_asc", "Price: low to high"),
            ("price_desc", "Price: high to low"),
            ("newest", "Newest"),
            ("name", "Name"),
        ),
    )
    page = forms.IntegerField(required=False, min_value=1)

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get("currency", "").upper()
        if currency:
            try:
                validate_iso_currency(currency)
            except ValidationError as error:
                self.add_error("currency", error)
            else:
                cleaned["currency"] = currency

        low = cleaned.get("minPrice")
        high = cleaned.get("maxPrice")
        if low is not None and high is not None and low > high:
            self.add_error("maxPrice", "Maximum price must be at least the minimum price.")
        if (low is not None or high is not None) and not currency:
            self.add_error("currency", "Choose a currency before filtering by price.")
        if currency:
            for field_name in ("minPrice", "maxPrice"):
                amount = cleaned.get(field_name)
                if amount is not None:
                    try:
                        major_to_minor(amount, currency)
                    except ValueError as error:
                        self.add_error(field_name, str(error))
        if (cleaned.get("sort") or "").startswith("price_") and not currency:
            self.add_error("sort", "Choose a currency before sorting by price.")
        return cleaned


def search_products(params):
    form = ProductSearchForm(params)
    if not form.is_valid():
        return form, Product.objects.none()

    filters = form.cleaned_data
    products = (
        Product.objects.filter(publication_status=Product.PublicationStatus.PUBLISHED)
        .annotate(min_price=Min("variants__price_minor", filter=Q(variants__is_active=True)))
        .filter(min_price__isnull=False)
        .select_related("category")
    )
    query = filters.get("q", "").strip()
    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query)
        )
    category = filters.get("category")
    if category:
        products = products.filter(category__slug=category, category__is_active=True)
    currency = filters.get("currency")
    if currency:
        products = products.filter(currency=currency)
    minimum = filters.get("minPrice")
    maximum = filters.get("maxPrice")
    if minimum is not None:
        products = products.filter(min_price__gte=major_to_minor(minimum, currency))
    if maximum is not None:
        products = products.filter(min_price__lte=major_to_minor(maximum, currency))

    ordering = {
        "relevance": ("name",),
        "price_asc": ("min_price", "name"),
        "price_desc": ("-min_price", "name"),
        "newest": ("-created_at", "name"),
        "name": ("name",),
    }[filters.get("sort") or "relevance"]
    return form, products.order_by(*ordering)
