from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("action", "actor", "subject", "request_id", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("actor__email", "subject__email", "request_id")
    readonly_fields = ("action", "actor", "subject", "request_id", "created_at")
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
