import uuid

from django.contrib.auth import get_user_model
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
from promotions.models import PointEntry
from promotions.points import (
    PointsError,
    adjust_points,
    credit_paid_order_points,
    get_points_balance,
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
        earned = PointEntry.objects.get(order=self.order)

        reversed_entry = reverse_point_entry(
            earned,
            points=5,
            event_key="refund:order-line:1:part-1",
            reason="Partial refund",
        )
        replay = reverse_point_entry(
            earned,
            points=5,
            event_key="refund:order-line:1:part-1",
            reason="Partial refund",
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

    def test_manual_adjustment_requires_the_dedicated_permission_and_reason(self):
        staff = get_user_model().objects.create_user(
            "staff@example.com", "Another-safe-passphrase-85!", is_staff=True
        )

        with self.assertRaises(PermissionDenied):
            adjust_points(
                self.user,
                points=10,
                event_key="manual:unauthorized",
                actor=staff,
                reason="Support adjustment",
            )

    def test_authorized_manual_adjustment_is_idempotent(self):
        staff = get_user_model().objects.create_superuser(
            "admin@example.com", "Admin-safe-passphrase-87!"
        )

        entry = adjust_points(
            self.user,
            points=10,
            event_key="manual:approved-1",
            actor=staff,
            reason="Approved support correction",
        )
        replay = adjust_points(
            self.user,
            points=10,
            event_key="manual:approved-1",
            actor=staff,
            reason="Approved support correction",
        )

        self.assertEqual(entry.pk, replay.pk)
        self.assertEqual(get_points_balance(self.user), 10)

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
