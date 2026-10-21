from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, IntegerField, Value, When

from .forms import (
    ProductReviewModerationForm,
    ReviewAppealResolutionForm,
    ReviewReportResolutionForm,
)
from .models import (
    ProductReview,
    ReviewAppeal,
    ReviewAppealEvent,
    ReviewModerationEvent,
    ReviewReport,
    ReviewReportEvent,
)
from .services import moderate_review, resolve_review_appeal, resolve_review_report


class ReviewModerationEventInline(admin.TabularInline):
    model = ReviewModerationEvent
    extra = 0
    can_delete = False
    fields = ("previous_status", "new_status", "moderator", "reason", "appeal", "created_at")
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
                raise ValidationError(
                    "A reason can only be changed when recording a moderation action."
                )
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


class ReviewReportEventInline(admin.TabularInline):
    model = ReviewReportEvent
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


class ReviewAppealEventInline(admin.TabularInline):
    model = ReviewAppealEvent
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


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    form = ReviewReportResolutionForm
    list_display = ("review", "reporter", "category", "status", "created_at", "resolved_at")
    list_filter = ("status", "category", "created_at")
    search_fields = ("review__product__name", "reporter__email", "details")
    fields = (
        "review",
        "reporter",
        "category",
        "details",
        "created_at",
        "status",
        "resolution_note",
        "resolved_by",
        "resolved_at",
    )
    readonly_fields = (
        "review",
        "reporter",
        "category",
        "details",
        "created_at",
        "resolved_by",
        "resolved_at",
    )
    inlines = (ReviewReportEventInline,)

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

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("review__product", "reporter", "resolved_by")
            .annotate(
                _queue_order=Case(
                    When(status=ReviewReport.Status.OPEN, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("_queue_order", "created_at", "pk")
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status != ReviewReport.Status.OPEN:
            return tuple(field.name for field in obj._meta.fields)
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change:
            raise PermissionDenied(
                "Review reports can only be resolved by an authorized moderator."
            )
        existing = ReviewReport.objects.get(pk=obj.pk)
        if existing.status == obj.status:
            if existing.resolution_note != obj.resolution_note:
                raise ValidationError("A resolution note can only change when resolving a report.")
            return
        resolved = resolve_review_report(
            existing,
            status=obj.status,
            moderator=request.user,
            reason=obj.resolution_note,
        )
        obj.status = resolved.status
        obj.resolution_note = resolved.resolution_note
        obj.resolved_by = resolved.resolved_by
        obj.resolved_at = resolved.resolved_at


@admin.register(ReviewAppeal)
class ReviewAppealAdmin(admin.ModelAdmin):
    form = ReviewAppealResolutionForm
    list_display = ("review", "customer", "status", "created_at", "resolved_at")
    list_filter = ("status", "created_at")
    search_fields = ("review__product__name", "customer__email", "reason")
    fields = (
        "review",
        "customer",
        "reason",
        "created_at",
        "status",
        "resolution_note",
        "resolved_by",
        "resolved_at",
    )
    readonly_fields = ("review", "customer", "reason", "created_at", "resolved_by", "resolved_at")
    inlines = (ReviewAppealEventInline,)

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

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("review__product", "customer", "resolved_by")
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status != ReviewAppeal.Status.PENDING:
            return tuple(field.name for field in obj._meta.fields)
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not change:
            raise PermissionDenied(
                "Review appeals can only be resolved by an authorized moderator."
            )
        existing = ReviewAppeal.objects.get(pk=obj.pk)
        if existing.status == obj.status:
            if existing.resolution_note != obj.resolution_note:
                raise ValidationError("A resolution note can only change when resolving an appeal.")
            return
        resolved = resolve_review_appeal(
            existing,
            status=obj.status,
            moderator=request.user,
            reason=obj.resolution_note,
        )
        obj.status = resolved.status
        obj.resolution_note = resolved.resolution_note
        obj.resolved_by = resolved.resolved_by
        obj.resolved_at = resolved.resolved_at
