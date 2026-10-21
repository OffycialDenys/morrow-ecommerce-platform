from django.contrib import admin

from .models import PointEntry


@admin.register(PointEntry)
class PointEntryAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "event_type",
        "points",
        "order",
        "actor",
        "created_at",
    )
    list_filter = ("event_type", "created_at")
    search_fields = ("customer__email", "event_key", "reason", "order__order_number")
    readonly_fields = (
        "customer",
        "order",
        "order_line",
        "event_type",
        "points",
        "event_key",
        "reversal_of",
        "actor",
        "reason",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
