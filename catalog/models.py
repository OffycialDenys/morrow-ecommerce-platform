from datetime import timedelta

from babel.core import get_global
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db import models
from django.db.models import F, Q
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
    points_per_unit = models.PositiveIntegerField(default=0)
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


class SupplierSyncRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Completed with errors"
        FAILED = "failed", "Failed"

    supplier_code = models.CharField(
        max_length=40,
        validators=[RegexValidator(r"^[a-z][a-z0-9_]{0,39}$")],
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="supplier_sync_runs",
        null=True,
        blank=True,
    )
    dry_run = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    records_seen = models.PositiveIntegerField(default=0)
    staged_count = models.PositiveIntegerField(default=0)
    validated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_summary = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    last_progress_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    records_seen=(
                        F("staged_count")
                        + F("validated_count")
                        + F("skipped_count")
                        + F("failed_count")
                    )
                ),
                name="cat_sync_counts_match_records",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="running", finished_at__isnull=True)
                    | Q(
                        status__in=("succeeded", "partial", "failed"),
                        finished_at__isnull=False,
                    )
                ),
                name="cat_sync_finish_state_consistent",
            ),
            models.UniqueConstraint(
                fields=("supplier_code",),
                condition=Q(status="running"),
                name="cat_one_running_supplier_sync",
            ),
        ]
        indexes = [
            models.Index(
                fields=("supplier_code", "-started_at"), name="cat_sync_provider_recent_idx"
            ),
        ]
        permissions = [
            ("run_supplier_sync", "Can run supplier catalog imports"),
            ("view_supplier_sync", "Can view supplier catalog import history"),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_code} sync {self.pk} ({self.status})"


class ImmutableSupplierDataQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Supplier import records cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Supplier import records cannot be changed.")

    def delete(self):
        raise ValidationError("Supplier import records cannot be deleted.")


class SupplierCatalogSnapshot(models.Model):
    sync_run = models.ForeignKey(
        SupplierSyncRun, on_delete=models.PROTECT, related_name="snapshots"
    )
    supplier_code = models.CharField(max_length=40)
    supplier_product_id = models.CharField(max_length=120)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    image_url = models.URLField(
        max_length=500,
        blank=True,
        validators=[https_url_validator],
        help_text="Stored for staff review only; this URL is never fetched by the application.",
    )
    variants = models.JSONField()
    source_digest = models.CharField(max_length=64)
    observed_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableSupplierDataQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("sync_run", "supplier_product_id"),
                name="cat_sync_product_snapshot_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=("supplier_code", "supplier_product_id", "-created_at"),
                name="cat_sup_snap_recent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.supplier_code} product {self.supplier_product_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Supplier catalog snapshots cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Supplier catalog snapshots cannot be deleted.")


class SupplierSyncItem(models.Model):
    class Outcome(models.TextChoices):
        STAGED = "staged", "Staged for review"
        VALIDATED = "validated", "Validated (dry run)"
        DUPLICATE = "duplicate", "Duplicate skipped"
        FAILED = "failed", "Validation or import failed"

    sync_run = models.ForeignKey(
        SupplierSyncRun, on_delete=models.PROTECT, related_name="items"
    )
    record_number = models.PositiveIntegerField()
    supplier_product_id = models.CharField(max_length=120, blank=True)
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    error_code = models.CharField(max_length=40, blank=True)
    error_message = models.CharField(max_length=400, blank=True)
    snapshot = models.OneToOneField(
        SupplierCatalogSnapshot,
        on_delete=models.PROTECT,
        related_name="sync_item",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableSupplierDataQuerySet.as_manager()

    class Meta:
        ordering = ("record_number", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("sync_run", "record_number"), name="cat_sync_item_number_unique"
            ),
            models.CheckConstraint(
                condition=(
                    Q(outcome="staged", snapshot__isnull=False, error_code="", error_message="")
                    | Q(
                        outcome="validated",
                        snapshot__isnull=True,
                        error_code="",
                        error_message="",
                    )
                    | Q(
                        outcome="duplicate",
                        snapshot__isnull=True,
                        error_code="",
                        error_message="",
                    )
                    | Q(
                        outcome="failed",
                        snapshot__isnull=True,
                        error_code__gt="",
                        error_message__gt="",
                    )
                ),
                name="cat_sync_item_outcome_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=("sync_run", "outcome"), name="cat_sync_item_outcome_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sync_run}: record {self.record_number} ({self.outcome})"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Supplier import records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Supplier import records cannot be deleted.")
