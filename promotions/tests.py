import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductVariant
from commerce.models import Order
from commerce.payments import (
    VerifiedPaymentEvent,
    create_payment_attempt,
    process_verified_payment_event,
)
from commerce.services import create_order_snapshot
from promotions.models import (
    PointAdjustmentAuditEvent,
    PointAdjustmentRequest,
    PointEntry,
)
from promotions.points import (
    PointsError,
    approve_point_adjustment,
    credit_paid_order_points,
    get_points_balance,
    reject_point_adjustment,
    request_point_adjustment,
    reverse_point_entry,
)


class PointsServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "points@example.com", "A-long-and-safe-passphrase-84!"
        )
        self.product = Product.objects.create(
            name="Linen Napkin Set",
            slug="linen-napkin-set",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="NAPKIN-OAT",
            price_minor=2400,
            points_per_unit=7,
            available_units=8,
        )
        self.order = create_order_snapshot(
            self.user,
            {"cart_items": {str(self.variant.pk): 2}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=0,
            tax_minor=0,
            currency="USD",
        )
        self.order.payment_status = Order.PaymentStatus.PAID
        self.order.save(update_fields=("payment_status",))

    @override_settings(POINTS_PROGRAM_ENABLED=True)
    def test_paid_order_awards_configured_per_unit_points_once(self):
        line = self.order.lines.get()
        self.variant.points_per_unit = 2
        self.variant.save(update_fields=("points_per_unit",))

        first = credit_paid_order_points(self.order)
        replay = credit_paid_order_points(self.order)

        self.assertEqual(line.points_per_unit, 7)
        self.assertEqual(first, 14)
        self.assertEqual(replay, 0)
        self.assertEqual(get_points_balance(self.user), 14)
        self.assertEqual(PointEntry.objects.filter(order=self.order).count(), 1)

    @override_settings(POINTS_PROGRAM_ENABLED=False)
    def test_points_are_not_awarded_while_program_is_disabled(self):
        total = credit_paid_order_points(self.order)

        self.assertEqual(total, 0)
        self.assertFalse(PointEntry.objects.exists())

    @override_settings(POINTS_PROGRAM_ENABLED=True)
    def test_unpaid_order_cannot_receive_purchase_points(self):
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("payment_status",))

        with self.assertRaises(PointsError):
            credit_paid_order_points(self.order)

        self.assertFalse(PointEntry.objects.exists())

    @override_settings(POINTS_PROGRAM_ENABLED=True)
    def test_verified_payment_transition_credits_the_order_once(self):
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("payment_status",))
        attempt = create_payment_attempt(
            self.order, provider="stripe", idempotency_key=uuid.uuid4()
        )
        attempt.provider_reference = "cs_points_1"
        attempt.save(update_fields=("provider_reference",))
        event = VerifiedPaymentEvent(
            provider="stripe",
            event_id="evt_points_1",
            event_type="checkout.session.completed",
            attempt_key=attempt.idempotency_key,
            provider_reference="cs_points_1",
            transaction_reference="pi_points_1",
            payload_digest="a" * 64,
            outcome="paid",
            amount_minor=self.order.total_minor,
            currency="USD",
        )

        result = process_verified_payment_event(event)
        retry = process_verified_payment_event(
            VerifiedPaymentEvent(**{**event.__dict__, "event_id": "evt_points_2"})
        )

        self.assertEqual((result, retry), ("processed", "processed"))
        self.assertEqual(get_points_balance(self.user), 14)
        self.assertEqual(PointEntry.objects.filter(order=self.order).count(), 1)

    @override_settings(POINTS_PROGRAM_ENABLED=True)
    def test_partial_reversal_is_idempotent_and_cannot_exceed_earned_points(self):
        credit_paid_order_points(self.order)
        actor = get_user_model().objects.create_superuser(
            "reversal-admin@example.com", "Reversal-admin-safe-passphrase-96!"
        )
        earned = PointEntry.objects.get(order=self.order)

        reversed_entry = reverse_point_entry(
            earned,
            points=5,
            event_key="refund:order-line:1:part-1",
            reason="Partial refund",
            actor=actor,
        )
        replay = reverse_point_entry(
            earned,
            points=5,
            event_key="refund:order-line:1:part-1",
            reason="Partial refund",
            actor=actor,
        )

        self.assertEqual(reversed_entry.pk, replay.pk)
        self.assertEqual(reversed_entry.points, -5)
        self.assertEqual(get_points_balance(self.user), 9)
        with self.assertRaises(PointsError):
            reverse_point_entry(
                earned,
                points=10,
                event_key="refund:order-line:1:part-2",
                reason="Over reversal",
                actor=actor,
            )

    def test_reversal_requires_an_authorized_actor(self):
        original = PointEntry.objects.create(
            customer=self.user,
            event_type=PointEntry.EventType.ADJUSTMENT,
            points=5,
            event_key="manual:reversible-entry",
            reason="Test source entry",
        )
        staff = get_user_model().objects.create_user(
            "unauthorized-reversal@example.com",
            "Unauthorized-reversal-safe-passphrase-97!",
            is_staff=True,
        )

        with self.assertRaises(PermissionDenied):
            reverse_point_entry(
                original,
                points=1,
                event_key="refund:unauthorized-1",
                reason="Unauthorized reversal",
                actor=staff,
            )
        with self.assertRaises(PermissionDenied):
            reverse_point_entry(
                original,
                points=1,
                event_key="refund:missing-actor-1",
                reason="Missing actor reversal",
                actor=None,
            )

    @override_settings(POINTS_PROGRAM_ENABLED=True)
    def test_ledger_entries_are_immutable(self):
        credit_paid_order_points(self.order)
        entry = PointEntry.objects.get(order=self.order)
        entry.points += 1

        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            PointEntry.objects.get(pk=entry.pk).delete()

    def test_manual_adjustment_request_requires_the_dedicated_permission(self):
        staff = get_user_model().objects.create_user(
            "staff@example.com", "Another-safe-passphrase-85!", is_staff=True
        )

        with self.assertRaises(PermissionDenied):
            request_point_adjustment(
                self.user,
                points=10,
                event_key="manual:unauthorized",
                requester=staff,
                reason="Support adjustment",
            )

    def test_authorized_manual_adjustment_requires_independent_approval(self):
        requester = get_user_model().objects.create_superuser(
            "requester@example.com", "Requester-safe-passphrase-87!"
        )
        approver = get_user_model().objects.create_superuser(
            "approver@example.com", "Approver-safe-passphrase-88!"
        )

        adjustment = request_point_adjustment(
            self.user,
            points=10,
            event_key="manual:approved-1",
            requester=requester,
            reason="Approved support correction",
        )
        replay_request = request_point_adjustment(
            self.user,
            points=10,
            event_key="manual:approved-1",
            requester=requester,
            reason="Approved support correction",
        )
        self.assertEqual(adjustment.pk, replay_request.pk)
        self.assertEqual(adjustment.status, PointAdjustmentRequest.Status.PENDING)
        self.assertFalse(PointEntry.objects.exists())

        with self.assertRaises(PointsError):
            approve_point_adjustment(adjustment, approver=requester)

        entry = approve_point_adjustment(adjustment, approver=approver)
        replay = approve_point_adjustment(adjustment, approver=approver)

        self.assertEqual(entry.pk, replay.pk)
        self.assertEqual(get_points_balance(self.user), 10)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, PointAdjustmentRequest.Status.APPROVED)
        self.assertEqual(adjustment.resolved_by_id, approver.pk)
        self.assertEqual(adjustment.audit_events.count(), 2)
        self.assertEqual(adjustment.ledger_entry_id, entry.pk)
        with self.assertRaises(ValidationError):
            adjustment.audit_events.update(event_type=PointAdjustmentAuditEvent.EventType.REJECTED)

    def test_adjustment_request_details_cannot_be_changed_or_deleted(self):
        requester = get_user_model().objects.create_superuser(
            "immutable-requester@example.com", "Immutable-requester-safe-passphrase-93!"
        )
        adjustment = request_point_adjustment(
            self.user,
            points=10,
            event_key="manual:immutable-request-1",
            requester=requester,
            reason="Original correction request",
        )

        adjustment.reason = "Changed after submission"
        with self.assertRaises(ValidationError):
            adjustment.save()
        with self.assertRaises(ValidationError):
            PointAdjustmentRequest.objects.filter(pk=adjustment.pk).update(points=20)
        with self.assertRaises(ValidationError):
            adjustment.delete()

        adjustment.refresh_from_db()
        self.assertEqual(adjustment.reason, "Original correction request")
        self.assertEqual(adjustment.audit_events.count(), 1)

    def test_approval_rejects_a_ledger_event_key_collision(self):
        requester = get_user_model().objects.create_superuser(
            "collision-requester@example.com", "Collision-requester-safe-passphrase-94!"
        )
        approver = get_user_model().objects.create_superuser(
            "collision-approver@example.com", "Collision-approver-safe-passphrase-95!"
        )
        adjustment = request_point_adjustment(
            self.user,
            points=6,
            event_key="manual:collision-1",
            requester=requester,
            reason="Correction request",
        )
        PointEntry.objects.create(
            customer=self.user,
            event_type=PointEntry.EventType.ADJUSTMENT,
            points=6,
            event_key=adjustment.event_key,
            actor=approver,
            reason=adjustment.reason,
        )

        with self.assertRaises(PointsError):
            approve_point_adjustment(adjustment, approver=approver)

        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, PointAdjustmentRequest.Status.PENDING)
        self.assertIsNone(adjustment.ledger_entry_id)
        self.assertEqual(adjustment.audit_events.count(), 1)

    def test_rejected_adjustment_is_audited_without_a_ledger_entry(self):
        requester = get_user_model().objects.create_superuser(
            "reject-requester@example.com", "Reject-requester-safe-passphrase-89!"
        )
        reviewer = get_user_model().objects.create_superuser(
            "reject-reviewer@example.com", "Reject-reviewer-safe-passphrase-90!"
        )
        adjustment = request_point_adjustment(
            self.user,
            points=-4,
            event_key="manual:rejected-1",
            requester=requester,
            reason="Correction requested by support.",
        )

        with self.assertRaises(PointsError):
            reject_point_adjustment(adjustment, reviewer=requester, reason="Not approved.")
        rejected = reject_point_adjustment(
            adjustment, reviewer=reviewer, reason="Evidence was incomplete."
        )

        self.assertEqual(rejected.status, PointAdjustmentRequest.Status.REJECTED)
        self.assertIsNone(rejected.ledger_entry_id)
        self.assertEqual(rejected.audit_events.count(), 2)
        self.assertEqual(get_points_balance(self.user), 0)

    def test_adjustment_admin_exposes_request_and_approval_as_separate_actions(self):
        requester = get_user_model().objects.create_user(
            "admin-requester@example.com", "Admin-requester-safe-passphrase-91!", is_staff=True
        )
        approver = get_user_model().objects.create_user(
            "admin-approver@example.com", "Admin-approver-safe-passphrase-92!", is_staff=True
        )
        request_permission = Permission.objects.get(
            content_type__app_label="promotions", codename="request_point_adjustment"
        )
        approve_permission = Permission.objects.get(
            content_type__app_label="promotions", codename="approve_point_adjustment"
        )
        requester.user_permissions.add(request_permission)
        approver.user_permissions.add(approve_permission)
        add_url = reverse("admin:promotions_pointadjustmentrequest_add")
        self.client.force_login(requester)

        response = self.client.post(
            add_url,
            {
                "customer": self.user.pk,
                "points": "6",
                "event_key": "manual:admin-flow-1",
                "reason": "Approved support correction",
                "_save": "Save",
                "audit_events-TOTAL_FORMS": "0",
                "audit_events-INITIAL_FORMS": "0",
                "audit_events-MIN_NUM_FORMS": "0",
                "audit_events-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 302)
        adjustment = PointAdjustmentRequest.objects.get(event_key="manual:admin-flow-1")
        self.assertEqual(adjustment.status, PointAdjustmentRequest.Status.PENDING)

        self.client.force_login(approver)
        response = self.client.post(
            reverse("admin:promotions_pointadjustmentrequest_changelist"),
            {
                "action": "approve_requests",
                "index": "0",
                "_selected_action": str(adjustment.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, PointAdjustmentRequest.Status.APPROVED)
        self.assertEqual(adjustment.ledger_entry.points, 6)
        self.assertEqual(
            PointAdjustmentAuditEvent.objects.filter(adjustment=adjustment).count(), 2
        )

        rejected_adjustment = request_point_adjustment(
            self.user,
            points=-2,
            event_key="manual:admin-flow-rejection-1",
            requester=requester,
            reason="A second support correction",
        )
        list_url = reverse("admin:promotions_pointadjustmentrequest_changelist")
        response = self.client.post(
            list_url,
            {
                "action": "reject_requests",
                "index": "0",
                "_selected_action": str(rejected_adjustment.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reason for rejection")

        response = self.client.post(
            list_url,
            {
                "action": "reject_requests",
                "index": "0",
                "select_across": "0",
                "_selected_action": str(rejected_adjustment.pk),
                "apply": "yes",
                "reason": "The support evidence did not match the account.",
            },
        )

        self.assertEqual(response.status_code, 302)
        rejected_adjustment.refresh_from_db()
        self.assertEqual(rejected_adjustment.status, PointAdjustmentRequest.Status.REJECTED)
        self.assertEqual(
            rejected_adjustment.resolution_reason,
            "The support evidence did not match the account.",
        )

    def test_customer_points_page_is_private_and_shows_their_ledger(self):
        other = get_user_model().objects.create_user(
            "another@example.com", "Yet-another-safe-passphrase-86!"
        )
        PointEntry.objects.create(
            customer=self.user,
            event_type=PointEntry.EventType.ADJUSTMENT,
            points=8,
            event_key="manual:seeded-entry",
            reason="Testing entry",
        )
        self.client.force_login(other)

        response = self.client.get(reverse("accounts:points"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0 points")
        self.assertNotContains(response, "Testing entry")

    @override_settings(POINTS_PROGRAM_ENABLED=True)
    def test_order_detail_shows_net_points_from_that_order(self):
        credit_paid_order_points(self.order)
        self.client.force_login(self.user)

        response = self.client.get(reverse("accounts:order-detail", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Net points from this order: 14")
