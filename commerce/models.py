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
        CANCELLED = "cancelled", "Cancelled"
        NEEDS_REVIEW = "needs_review", "Needs payment review"

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


class PaymentAttempt(models.Model):
    class Provider(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        PAYPAL = "paypal", "PayPal"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        AWAITING_CUSTOMER = "awaiting_customer", "Awaiting customer"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payment_attempts")
    provider = models.CharField(max_length=12, choices=Provider.choices)
    idempotency_key = models.UUIDField(unique=True, editable=False)
    provider_reference = models.CharField(max_length=200, blank=True)
    transaction_reference = models.CharField(max_length=200, blank=True)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, validators=[validate_iso_currency])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "provider_reference"),
                condition=~Q(provider_reference=""),
                name="commerce_payment_provider_ref_unique",
            ),
            models.UniqueConstraint(
                fields=("provider", "transaction_reference"),
                condition=~Q(transaction_reference=""),
                name="commerce_payment_transaction_ref_unique",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(status__in=("created", "awaiting_customer")),
                name="commerce_one_open_payment_per_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} attempt for {self.order}"


class PaymentEvent(models.Model):
    class Status(models.TextChoices):
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        REJECTED = "rejected", "Rejected"

    provider = models.CharField(max_length=12, choices=PaymentAttempt.Provider.choices)
    provider_event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=120)
    provider_reference = models.CharField(max_length=200, blank=True)
    transaction_reference = models.CharField(max_length=200, blank=True)
    attempt = models.ForeignKey(
        PaymentAttempt,
        on_delete=models.SET_NULL,
        related_name="events",
        null=True,
        blank=True,
    )
    payload_digest = models.CharField(max_length=64)
    reported_amount_minor = models.PositiveBigIntegerField()
    reported_currency = models.CharField(max_length=16)
    status = models.CharField(max_length=12, choices=Status.choices)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "provider_event_id"),
                name="commerce_payment_event_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} event {self.provider_event_id}"
