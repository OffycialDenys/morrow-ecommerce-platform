from django.contrib import admin

from .models import Order, OrderLine


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
