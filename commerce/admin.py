from django.contrib import admin

from .models import Order, OrderLine, PaymentAttempt, PaymentEvent


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    can_delete = False
    readonly_fields = (
        "variant",
        "product_name",
        "variant_label",
        "sku",
        "attributes",
        "quantity",
        "unit_price_minor",
        "points_per_unit",
        "line_total_minor",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "user",
        "currency",
        "total_minor",
        "payment_status",
        "fulfillment_status",
        "created_at",
    )
    list_filter = ("payment_status", "fulfillment_status", "currency")
    search_fields = ("order_number", "buyer_email", "user__email")
    readonly_fields = (
        "order_number",
        "idempotency_key",
        "request_fingerprint",
        "user",
        "buyer_email",
        "currency",
        "subtotal_minor",
        "shipping_minor",
        "tax_minor",
        "total_minor",
        "payment_status",
        "fulfillment_status",
        "created_at",
        "updated_at",
    )
    inlines = (OrderLineInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("order", "provider", "status", "amount_minor", "currency", "created_at")
    list_filter = ("provider", "status", "currency")
    search_fields = ("provider_reference", "order__order_number", "order__buyer_email")
    readonly_fields = (
        "order",
        "provider",
        "idempotency_key",
        "provider_reference",
        "transaction_reference",
        "amount_minor",
        "currency",
        "status",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = (
        "provider",
        "provider_event_id",
        "transaction_reference",
        "event_type",
        "status",
        "received_at",
    )
    list_filter = ("provider", "status", "event_type")
    search_fields = (
        "provider_event_id",
        "provider_reference",
        "transaction_reference",
        "attempt__provider_reference",
        "attempt__order__order_number",
    )
    readonly_fields = (
        "provider",
        "provider_event_id",
        "event_type",
        "provider_reference",
        "transaction_reference",
        "attempt",
        "payload_digest",
        "reported_amount_minor",
        "reported_currency",
        "status",
        "received_at",
        "processed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
