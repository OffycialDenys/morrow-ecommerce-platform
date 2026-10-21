from django.contrib import admin

from .models import Category, Product, ProductVariant


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = (
        "sku",
        "label",
        "attributes",
        "price_minor",
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
