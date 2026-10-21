from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError

from .forms import ProductReviewModerationForm
from .models import ProductReview, ReviewModerationEvent
from .services import moderate_review


class ReviewModerationEventInline(admin.TabularInline):
    model = ReviewModerationEvent
    extra = 0
    can_delete = False
    fields = ("previous_status", "new_status", "moderator", "reason", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("reviews.moderate_review")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    form = ProductReviewModerationForm
    list_display = ("product", "customer", "rating", "status", "created_at", "moderated_at")
    list_filter = ("status", "rating", "created_at")
    search_fields = ("product__name", "customer__email", "body")
    fields = (
        "product",
        "order_line",
        "customer",
        "rating",
        "body",
        "status",
        "moderation_reason",
        "moderated_by",
        "moderated_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "product",
        "order_line",
        "customer",
        "rating",
        "body",
        "moderated_by",
        "moderated_at",
        "created_at",
        "updated_at",
    )
    inlines = (ReviewModerationEventInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product", "customer", "moderated_by")

    def has_module_permission(self, request):
        return request.user.has_perm("reviews.moderate_review")

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("reviews.moderate_review")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("reviews.moderate_review")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change or not request.user.has_perm("reviews.moderate_review"):
            raise PermissionDenied("Reviews can only be changed by an authorized moderator.")

        existing = ProductReview.objects.get(pk=obj.pk)
        if existing.status == obj.status:
            if existing.moderation_reason != obj.moderation_reason:
                raise ValidationError("A reason can only be changed when recording a moderation action.")
            return

        moderated = moderate_review(
            review=existing,
            status=obj.status,
            moderator=request.user,
            reason=obj.moderation_reason,
        )
        obj.status = moderated.status
        obj.moderation_reason = moderated.moderation_reason
        obj.moderated_by = moderated.moderated_by
        obj.moderated_at = moderated.moderated_at
