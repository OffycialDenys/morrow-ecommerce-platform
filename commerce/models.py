import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from catalog.models import ProductVariant, validate_iso_currency


class Order(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Payment failed"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"

    class FulfillmentStatus(models.TextChoices):
        UNFULFILLED = "unfulfilled", "Unfulfilled"
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    order_number = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    idempotency_key = models.UUIDField(unique=True, editable=False)
    request_fingerprint = models.CharField(max_length=64, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders"
    )
    buyer_email = models.EmailField()
    currency = models.CharField(max_length=3, validators=[validate_iso_currency])
    subtotal_minor = models.PositiveBigIntegerField()
    shipping_minor = models.PositiveBigIntegerField()
    tax_minor = models.PositiveBigIntegerField()
    total_minor = models.PositiveBigIntegerField()
    payment_status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    fulfillment_status = models.CharField(
        max_length=20,
        choices=FulfillmentStatus.choices,
        default=FulfillmentStatus.UNFULFILLED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(total_minor=F("subtotal_minor") + F("shipping_minor") + F("tax_minor")),
                name="commerce_order_total_matches_parts",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "-created_at"), name="commerce_order_user_recent_idx")
        ]

    def __str__(self) -> str:
        return f"Order {self.order_number}"


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        related_name="order_lines",
        null=True,
        blank=True,
    )
    product_name = models.CharField(max_length=160)
    variant_label = models.CharField(max_length=120, blank=True)
    sku = models.CharField(max_length=80)
    attributes = models.JSONField(default=dict, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price_minor = models.PositiveBigIntegerField()
    line_total_minor = models.PositiveBigIntegerField()

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="commerce_order_line_qty_positive"
            ),
            models.CheckConstraint(
                condition=Q(line_total_minor=F("unit_price_minor") * F("quantity")),
                name="commerce_order_line_total_matches_parts",
            ),
            models.UniqueConstraint(fields=("order", "sku"), name="commerce_order_line_sku_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product_name} ({self.sku})"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Order line snapshots cannot be changed after creation.")
        super().save(*args, **kwargs)
