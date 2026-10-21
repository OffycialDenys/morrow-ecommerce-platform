from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse

from .giveaways import (
    GiveawayError,
    activate_giveaway,
    approve_giveaway_draw,
    approve_giveaway_rules,
    cancel_giveaway,
    close_giveaway,
    draw_giveaway_winners,
    reset_giveaway_rules,
    save_giveaway_campaign,
)
from .models import (
    GiveawayAuditEvent,
    GiveawayCampaign,
    GiveawayDraw,
    GiveawayDrawApproval,
    GiveawayEntry,
    PointEntry,
)

GIVEAWAY_PERMISSIONS = (
    "manage_giveaways",
    "approve_giveaway_rules",
    "activate_giveaways",
    "draw_giveaway_winners",
    "approve_giveaway_draw",
)


def _has_any_giveaway_permission(user):
    return any(user.has_perm(f"promotions.{permission}") for permission in GIVEAWAY_PERMISSIONS)


class GiveawayAuditEventInline(admin.TabularInline):
    model = GiveawayAuditEvent
    extra = 0
    max_num = 20
    can_delete = False
    fields = ("event_type", "actor", "details", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _has_any_giveaway_permission(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class GiveawayDrawApprovalInline(admin.TabularInline):
    model = GiveawayDrawApproval
    extra = 0
    can_delete = False
    fields = ("approver", "entry_count", "participant_digest", "approved_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _has_any_giveaway_permission(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


class GiveawayDrawInline(admin.TabularInline):
    model = GiveawayDraw
    extra = 0
    can_delete = False
    fields = (
        "entry_count",
        "winner_count",
        "participant_digest",
        "seed_hex",
        "seed_digest",
        "winner_entry_ids",
        "operator",
        "approver",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return _has_any_giveaway_permission(request.user)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GiveawayCampaign)
class GiveawayCampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "status",
        "entry_method",
        "starts_at",
        "ends_at",
        "entry_count",
        "prize_quantity",
    )
    list_filter = ("status", "entry_method")
    search_fields = ("name", "description", "legal_review_reference")
    prepopulated_fields = {"slug": ("name",)}
    fields = (
        "name",
        "slug",
        "description",
        "starts_at",
        "ends_at",
        "eligible_regions",
        "eligibility_criteria",
        "entry_method",
        "entries_per_qualifier",
        "max_entries_per_customer",
        "prize_description",
        "prize_quantity",
        "max_prizes_per_customer",
        "official_rules_url",
        "official_disclosures",
        "legal_review_reference",
        "status",
        "rules_approved_by",
        "rules_approved_at",
        "approved_rules_digest",
        "created_by",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "status",
        "rules_approved_by",
        "rules_approved_at",
        "approved_rules_digest",
        "created_by",
        "created_at",
        "updated_at",
    )
    actions = (
        "approve_rules",
        "activate_campaigns",
        "close_campaigns",
        "cancel_campaigns",
        "reset_to_draft",
        "approve_draws",
        "draw_winners",
    )
    inlines = (GiveawayAuditEventInline, GiveawayDrawApprovalInline, GiveawayDrawInline)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_entry_count=Count("entries"))

    @admin.display(description="Entries", ordering="_entry_count")
    def entry_count(self, obj):
        return obj._entry_count

    def _has_any_giveaway_permission(self, user):
        return _has_any_giveaway_permission(user)

    def has_module_permission(self, request):
        return self._has_any_giveaway_permission(request.user)

    def has_view_permission(self, request, obj=None):
        return self._has_any_giveaway_permission(request.user)

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("promotions.manage_giveaways")

    def has_add_permission(self, request):
        return request.user.has_perm("promotions.manage_giveaways")

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        rule_fields = [field for field in self.fields if field not in readonly]
        if not request.user.has_perm("promotions.manage_giveaways") or (
            obj is not None and obj.status != GiveawayCampaign.Status.DRAFT
        ):
            readonly.extend(rule_fields)
        return tuple(readonly)

    def save_model(self, request, obj, form, change):
        save_giveaway_campaign(obj, actor=request.user)

    def _run_campaign_action(self, request, queryset, operation, success_message):
        for campaign in queryset:
            try:
                operation(campaign)
            except (GiveawayError, PermissionDenied) as error:
                self.message_user(request, f"{campaign.name}: {error}", level=messages.ERROR)
            else:
                self.message_user(request, success_message.format(name=campaign.name))

    @admin.action(description="Approve the rules for selected draft giveaways", permissions=["approve_rules"])
    def approve_rules(self, request, queryset):
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: approve_giveaway_rules(campaign, reviewer=request.user),
            "Rules approved for {name}.",
        )

    def has_approve_rules_permission(self, request):
        return request.user.has_perm("promotions.approve_giveaway_rules")

    @admin.action(description="Activate approved giveaways in their entry window", permissions=["activate_campaigns"])
    def activate_campaigns(self, request, queryset):
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: activate_giveaway(campaign, actor=request.user),
            "Activated {name}.",
        )

    def has_activate_campaigns_permission(self, request):
        return request.user.has_perm("promotions.activate_giveaways")

    @admin.action(description="Close selected giveaways after their end time", permissions=["activate_campaigns"])
    def close_campaigns(self, request, queryset):
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: close_giveaway(campaign, actor=request.user),
            "Closed {name}.",
        )

    @admin.action(description="Cancel selected giveaways with a recorded reason", permissions=["activate_campaigns"])
    def cancel_campaigns(self, request, queryset):
        if request.POST.get("apply") != "yes":
            return render(
                request,
                "admin/promotions/giveawaycampaign/cancel_selected.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Cancel selected giveaways",
                    "campaigns": queryset,
                    "action_name": "cancel_campaigns",
                    "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
                    "select_across": request.POST.get("select_across", "0"),
                    "opts": self.model._meta,
                },
            )
        reason = request.POST.get("reason", "")
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: cancel_giveaway(campaign, actor=request.user, reason=reason),
            "Cancelled {name}.",
        )
        return redirect(reverse("admin:promotions_giveawaycampaign_changelist"))

    @admin.action(description="Return approved giveaways to draft for a rules revision", permissions=["manage_rules"])
    def reset_to_draft(self, request, queryset):
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: reset_giveaway_rules(campaign, actor=request.user),
            "Returned {name} to draft.",
        )

    def has_manage_rules_permission(self, request):
        return request.user.has_perm("promotions.manage_giveaways")

    @admin.action(description="Approve the closed entry pool for a drawing", permissions=["approve_drawing"])
    def approve_draws(self, request, queryset):
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: approve_giveaway_draw(campaign, approver=request.user),
            "Approved the drawing pool for {name}.",
        )

    def has_approve_drawing_permission(self, request):
        return request.user.has_perm("promotions.approve_giveaway_draw")

    @admin.action(description="Draw winners for selected approved campaigns", permissions=["draw_winners"])
    def draw_winners(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one giveaway to draw.", level=messages.ERROR)
            return
        self._run_campaign_action(
            request,
            queryset,
            lambda campaign: draw_giveaway_winners(campaign, operator=request.user),
            "Recorded the drawing for {name}.",
        )

    def has_draw_winners_permission(self, request):
        return request.user.has_perm("promotions.draw_giveaway_winners")


class ImmutableGiveawayAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or any(
            request.user.has_perm(f"promotions.{permission}") for permission in GIVEAWAY_PERMISSIONS
        )


@admin.register(GiveawayEntry)
class GiveawayEntryAdmin(ImmutableGiveawayAdmin):
    list_display = ("campaign", "customer", "ticket_number", "source", "region", "created_at")
    list_filter = ("campaign", "source", "region")
    search_fields = ("customer__email", "event_key", "source_event_key", "campaign__name")
    readonly_fields = (
        "campaign",
        "customer",
        "source",
        "source_event_key",
        "ticket_number",
        "event_key",
        "region",
        "actor",
        "created_at",
    )


@admin.register(GiveawayAuditEvent)
class GiveawayAuditEventAdmin(ImmutableGiveawayAdmin):
    list_display = ("campaign", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("campaign__name", "actor__email")
    readonly_fields = ("campaign", "actor", "event_type", "details", "created_at")


@admin.register(GiveawayDrawApproval)
class GiveawayDrawApprovalAdmin(ImmutableGiveawayAdmin):
    list_display = ("campaign", "approver", "entry_count", "approved_at")
    readonly_fields = ("campaign", "entry_count", "participant_digest", "approver", "approved_at")


@admin.register(GiveawayDraw)
class GiveawayDrawAdmin(ImmutableGiveawayAdmin):
    list_display = ("campaign", "entry_count", "winner_count", "operator", "approver", "created_at")
    readonly_fields = (
        "campaign",
        "entry_count",
        "winner_count",
        "participant_digest",
        "seed_hex",
        "seed_digest",
        "algorithm",
        "winner_entry_ids",
        "operator",
        "approver",
        "created_at",
    )


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
