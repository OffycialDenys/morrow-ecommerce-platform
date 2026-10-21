from django.contrib import admin
from django.db.models import OuterRef, Subquery

from .models import (
    Category,
    Product,
    ProductVariant,
    SupplierCatalogSnapshot,
    SupplierSyncItem,
    SupplierSyncRun,
)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = (
        "sku",
        "label",
        "attributes",
        "price_minor",
        "points_per_unit",
        "is_active",
        "inventory_source",
        "available_units",
        "inventory_observed_at",
        "supplier_variant_id",
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "currency",
        "publication_status",
        "supplier_code",
        "updated_at",
    )
    list_filter = ("publication_status", "category", "currency", "supplier_code")
    search_fields = ("name", "slug", "supplier_product_id", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = (ProductVariantInline,)


class SupplierImportReadOnlyAdmin(admin.ModelAdmin):
    view_all_permission = "catalog.view_supplier_sync"
    owner_lookup = "requested_by"

    def has_module_permission(self, request):
        return request.user.has_perm(self.view_all_permission) or request.user.has_perm(
            "catalog.run_supplier_sync"
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not request.user.has_perm(self.view_all_permission):
            queryset = queryset.filter(**{self.owner_lookup: request.user})
        return queryset


@admin.register(SupplierSyncRun)
class SupplierSyncRunAdmin(SupplierImportReadOnlyAdmin):
    list_display = (
        "supplier_code",
        "status",
        "dry_run",
        "records_seen",
        "staged_count",
        "validated_count",
        "skipped_count",
        "failed_count",
        "requested_by",
        "started_at",
        "finished_at",
        "last_successful_sync",
    )
    list_filter = ("supplier_code", "status", "dry_run", "started_at")
    search_fields = ("supplier_code", "error_summary", "requested_by__email")
    readonly_fields = tuple(field.name for field in SupplierSyncRun._meta.fields)
    owner_lookup = "requested_by"

    def get_queryset(self, request):
        successful_runs = SupplierSyncRun.objects.filter(
            supplier_code=OuterRef("supplier_code"),
            status=SupplierSyncRun.Status.SUCCEEDED,
            dry_run=False,
        ).order_by("-finished_at")
        if not request.user.has_perm(self.view_all_permission):
            successful_runs = successful_runs.filter(requested_by=request.user)
        return super().get_queryset(request).annotate(
            _last_successful_sync=Subquery(successful_runs.values("finished_at")[:1])
        )

    @admin.display(description="Last successful sync", ordering="_last_successful_sync")
    def last_successful_sync(self, obj):
        return obj._last_successful_sync


@admin.register(SupplierCatalogSnapshot)
class SupplierCatalogSnapshotAdmin(SupplierImportReadOnlyAdmin):
    list_display = (
        "supplier_code",
        "supplier_product_id",
        "name",
        "sync_run",
        "observed_at",
    )
    list_filter = ("supplier_code", "observed_at")
    search_fields = ("supplier_product_id", "name")
    readonly_fields = tuple(field.name for field in SupplierCatalogSnapshot._meta.fields)
    owner_lookup = "sync_run__requested_by"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sync_run")


@admin.register(SupplierSyncItem)
class SupplierSyncItemAdmin(SupplierImportReadOnlyAdmin):
    list_display = (
        "sync_run",
        "record_number",
        "supplier_product_id",
        "outcome",
        "error_code",
        "created_at",
    )
    list_filter = ("outcome", "error_code", "created_at")
    search_fields = ("supplier_product_id", "error_message", "sync_run__supplier_code")
    readonly_fields = tuple(field.name for field in SupplierSyncItem._meta.fields)
    owner_lookup = "sync_run__requested_by"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sync_run", "snapshot")
