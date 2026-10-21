import hashlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .giveaways import (
    GiveawayError,
    activate_giveaway,
    approve_giveaway_draw,
    approve_giveaway_rules,
    close_giveaway,
    draw_giveaway_winners,
    record_giveaway_entries,
    reset_giveaway_rules,
    save_giveaway_campaign,
)
from .models import GiveawayAuditEvent, GiveawayCampaign, GiveawayDraw, GiveawayEntry


class GiveawayLifecycleTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.creator = user_model.objects.create_user(
            "campaign-creator@example.com", "Campaign-creator-safe-passphrase-81!"
        )
        self.rules_reviewer = user_model.objects.create_user(
            "rules-reviewer@example.com", "Rules-reviewer-safe-passphrase-82!"
        )
        self.activator = user_model.objects.create_user(
            "campaign-activator@example.com", "Campaign-activator-safe-passphrase-83!"
        )
        self.draw_approver = user_model.objects.create_user(
            "draw-approver@example.com", "Draw-approver-safe-passphrase-84!"
        )
        self.draw_operator = user_model.objects.create_user(
            "draw-operator@example.com", "Draw-operator-safe-passphrase-85!"
        )
        self.customer = user_model.objects.create_user(
            "giveaway-customer@example.com", "Giveaway-customer-safe-passphrase-86!"
        )
        self.other_customer = user_model.objects.create_user(
            "other-giveaway-customer@example.com", "Other-customer-safe-passphrase-87!"
        )
        self.grant(self.creator, "manage_giveaways")
        self.grant(self.creator, "approve_giveaway_rules")
        self.grant(self.rules_reviewer, "approve_giveaway_rules")
        self.grant(self.rules_reviewer, "activate_giveaways")
        self.grant(self.activator, "activate_giveaways")
        self.grant(self.draw_approver, "approve_giveaway_draw")
        self.grant(self.draw_approver, "draw_giveaway_winners")
        self.grant(self.draw_operator, "draw_giveaway_winners")
        self.campaign = self.create_campaign()

    def grant(self, user, codename):
        permission = Permission.objects.get(
            content_type__app_label="promotions", codename=codename
        )
        user.user_permissions.add(permission)

    def create_campaign(self, *, ends_at=None, prize_quantity=1):
        now = timezone.now()
        campaign = GiveawayCampaign(
            name="Morrow spring giveaway",
            slug="morrow-spring-giveaway",
            description="A considered gift for the home.",
            starts_at=now - timedelta(hours=1),
            ends_at=ends_at or now + timedelta(hours=1),
            eligible_regions=["NG", "GB"],
            eligibility_criteria="Residents of Nigeria or Great Britain aged 18 or older.",
            entry_method=GiveawayCampaign.EntryMethod.FREE,
            entries_per_qualifier=2,
            max_entries_per_customer=4,
            prize_description="One Morrow home collection gift card.",
            prize_quantity=prize_quantity,
            max_prizes_per_customer=1,
            official_rules_url="https://example.com/giveaway-rules",
            official_disclosures="One entry request earns two tickets; one prize per person.",
            legal_review_reference="LEGAL-REVIEW-2026-014",
        )
        return save_giveaway_campaign(campaign, actor=self.creator)

    def approve_and_activate(self):
        approve_giveaway_rules(self.campaign, reviewer=self.rules_reviewer)
        return activate_giveaway(self.campaign, actor=self.activator)

    def test_activation_requires_legal_rules_approval_and_global_enablement(self):
        with self.assertRaises(GiveawayError):
            approve_giveaway_rules(self.campaign, reviewer=self.creator)
        approve_giveaway_rules(self.campaign, reviewer=self.rules_reviewer)

        with self.assertRaises(GiveawayError):
            activate_giveaway(self.campaign, actor=self.activator)
        self.assertEqual(
            GiveawayCampaign.objects.get(pk=self.campaign.pk).status,
            GiveawayCampaign.Status.APPROVED,
        )

        with override_settings(GIVEAWAYS_ENABLED=True):
            with self.assertRaises(GiveawayError):
                activate_giveaway(self.campaign, actor=self.rules_reviewer)
            active = activate_giveaway(self.campaign, actor=self.activator)

        self.assertEqual(active.status, GiveawayCampaign.Status.ACTIVE)
        self.assertEqual(active.rules_approved_by_id, self.rules_reviewer.pk)

    def test_incomplete_campaign_cannot_be_approved(self):
        self.campaign.official_rules_url = ""
        save_giveaway_campaign(self.campaign, actor=self.creator)

        with self.assertRaises(GiveawayError):
            approve_giveaway_rules(self.campaign, reviewer=self.rules_reviewer)

        self.assertEqual(self.campaign.audit_events.count(), 2)
        self.assertEqual(self.campaign.status, GiveawayCampaign.Status.DRAFT)

    def test_admin_actions_are_filtered_by_the_staff_permission(self):
        reviewer = get_user_model().objects.create_user(
            "approval-only@example.com", "Approval-only-safe-passphrase-88!", is_staff=True
        )
        self.grant(reviewer, "approve_giveaway_rules")
        request = RequestFactory().get(reverse("admin:promotions_giveawaycampaign_changelist"))
        request.user = reviewer
        model_admin = admin.site._registry[GiveawayCampaign]

        actions = model_admin.get_actions(request)

        self.assertIn("approve_rules", actions)
        self.assertNotIn("activate_campaigns", actions)
        self.assertNotIn("draw_winners", actions)

    def test_non_manager_can_review_campaign_details_without_editing_rules(self):
        reviewer = get_user_model().objects.create_user(
            "readonly-reviewer@example.com", "Readonly-reviewer-safe-passphrase-89!", is_staff=True
        )
        self.grant(reviewer, "approve_giveaway_rules")
        self.client.force_login(reviewer)

        response = self.client.get(
            reverse("admin:promotions_giveawaycampaign_change", args=[self.campaign.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.campaign.name)

    def test_approved_rules_must_be_reset_and_reapproved_before_changes(self):
        approve_giveaway_rules(self.campaign, reviewer=self.rules_reviewer)
        self.campaign.description = "Updated campaign description."

        with self.assertRaises(GiveawayError):
            save_giveaway_campaign(self.campaign, actor=self.creator)

        reset_giveaway_rules(self.campaign, actor=self.creator)
        save_giveaway_campaign(self.campaign, actor=self.creator)
        refreshed = GiveawayCampaign.objects.get(pk=self.campaign.pk)
        self.assertEqual(refreshed.status, GiveawayCampaign.Status.DRAFT)
        self.assertFalse(refreshed.approved_rules_digest)
        self.assertEqual(refreshed.audit_events.count(), 4)

    @override_settings(GIVEAWAYS_ENABLED=True)
    def test_free_entries_are_idempotent_limited_and_immutable(self):
        self.approve_and_activate()
        first = record_giveaway_entries(
            self.campaign,
            self.customer,
            source=GiveawayCampaign.EntryMethod.FREE,
            source_event_key="free:first-request",
            qualifier_count=1,
            region="ng",
        )
        repeated = record_giveaway_entries(
            self.campaign,
            self.customer,
            source=GiveawayCampaign.EntryMethod.FREE,
            source_event_key="free:first-request",
            qualifier_count=1,
            region="NG",
        )
        self.assertEqual([ticket.pk for ticket in first], [ticket.pk for ticket in repeated])
        self.assertEqual(len(first), 2)

        second = record_giveaway_entries(
            self.campaign,
            self.customer,
            source=GiveawayCampaign.EntryMethod.FREE,
            source_event_key="free:second-request",
            qualifier_count=1,
            region="NG",
        )
        self.assertEqual(len(second), 2)
        with self.assertRaises(GiveawayError):
            record_giveaway_entries(
                self.campaign,
                self.customer,
                source=GiveawayCampaign.EntryMethod.FREE,
                source_event_key="free:third-request",
                qualifier_count=1,
                region="NG",
            )
        with self.assertRaises(GiveawayError):
            record_giveaway_entries(
                self.campaign,
                self.other_customer,
                source=GiveawayCampaign.EntryMethod.FREE,
                source_event_key="free:first-request",
                qualifier_count=1,
                region="NG",
            )
        with self.assertRaises(PermissionDenied):
            record_giveaway_entries(
                self.campaign,
                self.customer,
                source=GiveawayCampaign.EntryMethod.MANUAL,
                source_event_key="manual:request",
                qualifier_count=1,
                region="NG",
            )

        with self.assertRaises(ValidationError):
            GiveawayEntry.objects.filter(pk=first[0].pk).delete()

    @override_settings(GIVEAWAYS_ENABLED=True)
    def test_free_entry_page_is_available_only_after_approval_and_activation(self):
        detail_url = reverse("promotions:giveaway-detail", args=[self.campaign.slug])
        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.approve_and_activate()

        response = self.client.get(detail_url)
        self.assertContains(response, "Free entry")
        self.assertContains(response, "official rules")
        self.client.force_login(self.customer)
        response = self.client.get(detail_url)
        nonce = response.context["entry_form"]["entry_nonce"].value()
        response = self.client.post(
            reverse("promotions:giveaway-enter", args=[self.campaign.slug]),
            {"entry_nonce": nonce, "region": "NG"},
        )
        self.assertRedirects(response, detail_url)
        self.assertEqual(
            GiveawayEntry.objects.filter(campaign=self.campaign, customer=self.customer).count(), 2
        )

    @override_settings(GIVEAWAYS_ENABLED=True)
    def test_draw_needs_independent_pool_approval_and_records_verifiable_seed(self):
        self.campaign.prize_quantity = 2
        self.campaign.max_entries_per_customer = 4
        self.campaign.ends_at = timezone.now() + timedelta(minutes=5)
        self.campaign = save_giveaway_campaign(self.campaign, actor=self.creator)
        self.approve_and_activate()
        for index, customer in enumerate((self.customer, self.other_customer)):
            record_giveaway_entries(
                self.campaign,
                customer,
                source=GiveawayCampaign.EntryMethod.FREE,
                source_event_key=f"free:draw:{index}",
                qualifier_count=1,
                region="NG",
            )

        with patch("promotions.giveaways.timezone.now", return_value=self.campaign.ends_at):
            closed = close_giveaway(self.campaign, actor=self.activator)
        self.assertEqual(closed.status, GiveawayCampaign.Status.CLOSED)
        approval = approve_giveaway_draw(self.campaign, approver=self.draw_approver)
        with self.assertRaises(GiveawayError):
            draw_giveaway_winners(self.campaign, operator=self.draw_approver)

        draw = draw_giveaway_winners(self.campaign, operator=self.draw_operator)
        self.assertEqual(draw.approver_id, approval.approver_id)
        self.assertEqual(draw.winner_count, 2)
        self.assertEqual(len(set(draw.winner_entry_ids)), 2)
        self.assertEqual(hashlib.sha256(bytes.fromhex(draw.seed_hex)).hexdigest(), draw.seed_digest)
        winners = GiveawayEntry.objects.filter(pk__in=draw.winner_entry_ids)
        self.assertEqual(winners.values("customer_id").distinct().count(), 2)
        self.assertEqual(
            GiveawayCampaign.objects.get(pk=self.campaign.pk).status,
            GiveawayCampaign.Status.DRAWN,
        )
        self.assertEqual(GiveawayDraw.objects.count(), 1)
        self.assertTrue(
            GiveawayAuditEvent.objects.filter(
                campaign=self.campaign, event_type=GiveawayAuditEvent.EventType.DRAWN
            ).exists()
        )
