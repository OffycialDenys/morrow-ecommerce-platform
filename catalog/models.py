from datetime import timedelta

from babel.core import get_global
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

currency_validator = RegexValidator(
    regex=r"^[A-Z]{3}$",
    message="Enter an uppercase, three-letter ISO currency code.",
)
supported_currency_codes = frozenset(get_global("all_currencies"))


def validate_iso_currency(value: str) -> None:
    currency_validator(value)
    if value not in supported_currency_codes:
        raise ValidationError("Enter a recognized ISO 4217 currency code.")


https_url_validator = URLValidator(schemes=["https"])


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.CharField(max_length=240, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    class PublicationStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending review"
        PUBLISHED = "published", "Published"
        UNPUBLISHED = "unpublished", "Unpublished"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
    )
    description = models.TextField(blank=True)
    image_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[https_url_validator],
        help_text="Optional HTTPS image URL. The application never fetches this URL server-side.",
    )
    currency = models.CharField(max_length=3, validators=[validate_iso_currency])
    publication_status = models.CharField(
        max_length=20,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
        db_index=True,
    )
    supplier_code = models.CharField(max_length=40, blank=True)
    supplier_product_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("supplier_code", "supplier_product_id"),
                condition=~Q(supplier_product_id=""),
                name="catalog_supplier_product_ref_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("publication_status", "name"), name="cat_prod_status_name_idx"),
            models.Index(fields=("category", "publication_status"), name="cat_prod_cat_status_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if self.currency:
            self.currency = self.currency.upper()
        super().save(*args, **kwargs)

    @property
    def minimum_price_minor(self) -> int | None:
        return self.variants.filter(is_active=True).aggregate(price=models.Min("price_minor"))[
            "price"
        ]

    @property
    def is_published(self) -> bool:
        return self.publication_status == self.PublicationStatus.PUBLISHED

    def __str__(self) -> str:
        return self.name


class ProductVariant(models.Model):
    class InventorySource(models.TextChoices):
        MANAGED = "managed", "Managed inventory"
        SUPPLIER = "supplier", "Supplier observation"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    sku = models.CharField(max_length=80, unique=True)
    label = models.CharField(max_length=120, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    price_minor = models.PositiveBigIntegerField()
    is_active = models.BooleanField(default=True)
    inventory_source = models.CharField(
        max_length=12,
        choices=InventorySource.choices,
        default=InventorySource.MANAGED,
    )
    available_units = models.PositiveIntegerField(null=True, blank=True)
    inventory_observed_at = models.DateTimeField(null=True, blank=True)
    supplier_variant_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("product__name", "sku")
        constraints = [
            models.UniqueConstraint(
                fields=("product", "supplier_variant_id"),
                condition=~Q(supplier_variant_id=""),
                name="catalog_supplier_variant_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(price_minor__gt=0), name="catalog_variant_price_positive"
            ),
        ]
        indexes = [
            models.Index(
                fields=("product", "is_active", "price_minor"), name="cat_var_prod_price_idx"
            ),
        ]

    def can_be_added_to_cart(
        self,
        *,
        now=None,
        supplier_freshness: timedelta | None = None,
    ) -> bool:
        if not self.is_active or not self.product.is_published or not self.available_units:
            return False
        if self.inventory_source == self.InventorySource.SUPPLIER:
            observed_at = self.inventory_observed_at
            if observed_at is None:
                return False
            now = now or timezone.now()
            supplier_freshness = supplier_freshness or timedelta(
                hours=getattr(settings, "SUPPLIER_STOCK_MAX_AGE_HOURS", 24)
            )
            if now - observed_at > supplier_freshness:
                return False
        return True

    def __str__(self) -> str:
        return f"{self.product.name} — {self.label or self.sku}"
