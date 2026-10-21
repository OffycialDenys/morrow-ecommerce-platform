import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductVariant
from commerce.models import Order, PaymentAttempt
from commerce.money import format_minor, major_to_minor, minor_to_major
from commerce.payments import (
    PaymentError,
    VerifiedPaymentEvent,
    create_payment_attempt,
    process_verified_payment_event,
    set_payment_attempt_reference,
)
from commerce.services import CartError, add_variant, cart_summary, create_order_snapshot


class MoneyTests(TestCase):
    def test_currency_exponents_are_respected_without_float_math(self):
        self.assertEqual(minor_to_major(1999, "USD"), Decimal("19.99"))
        self.assertEqual(major_to_minor(Decimal("1200"), "JPY"), 1200)
        self.assertEqual(major_to_minor(Decimal("1.125"), "KWD"), 1125)
        self.assertEqual(format_minor(1200, "JPY"), "JPY 1,200")


class CartFlowTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Everyday Tote",
            slug="everyday-tote",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="TOTE-NATURAL",
            label="Natural",
            price_minor=2450,
            available_units=4,
        )

    def test_cart_uses_catalog_price_and_ignores_forged_client_price(self):
        response = self.client.post(
            reverse("commerce:add"),
            {"variant_id": self.variant.pk, "quantity": 2, "price_minor": 1},
        )

        self.assertRedirects(response, reverse("commerce:cart"))
        summary = cart_summary(self.client.session)
        self.assertEqual(summary.subtotal_minor, 4900)
        self.assertEqual(self.client.session["cart_items"], {str(self.variant.pk): 2})

    def test_unavailable_variants_are_not_added(self):
        self.variant.available_units = 0
        self.variant.save()

        response = self.client.post(
            reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 1}
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("cart_items", self.client.session)

    def test_stale_supplier_observation_cannot_be_added(self):
        self.variant.inventory_source = ProductVariant.InventorySource.SUPPLIER
        self.variant.inventory_observed_at = timezone.now() - timedelta(hours=25)
        self.variant.save()

        response = self.client.post(
            reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 1}
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("cart_items", self.client.session)

    def test_mixed_currency_is_rejected(self):
        other_product = Product.objects.create(
            name="Travel Cup",
            slug="travel-cup",
            currency="JPY",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        other_variant = ProductVariant.objects.create(
            product=other_product, sku="TRAVEL-CUP", price_minor=1200, available_units=3
        )
        session = self.client.session
        add_variant(session, self.variant.pk, 1)

        with self.assertRaises(CartError):
            add_variant(session, other_variant.pk, 1)

        self.assertEqual(session["cart_items"], {str(self.variant.pk): 1})

    def test_quantity_cannot_exceed_confirmed_inventory(self):
        self.client.post(reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 1})

        response = self.client.post(
            reverse("commerce:update", args=[self.variant.pk]), {"quantity": 5}
        )

        self.assertRedirects(response, reverse("commerce:cart"))
        self.assertEqual(cart_summary(self.client.session).lines[0].quantity, 1)

    def test_cart_blocks_checkout_if_inventory_falls_below_cart_quantity(self):
        self.client.post(reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 3})
        self.variant.available_units = 2
        self.variant.save()

        summary = cart_summary(self.client.session)

        self.assertFalse(summary.can_continue)
        self.assertIsNone(summary.subtotal_minor)
        self.assertFalse(summary.lines[0].available)

    def test_order_snapshot_is_idempotent_and_keeps_catalog_values(self):
        user = get_user_model().objects.create_user("buyer@example.com", "passphrase-1234")
        self.client.post(reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 2})
        session = self.client.session
        idempotency_key = uuid.uuid4()

        order = create_order_snapshot(
            user,
            session,
            idempotency_key=idempotency_key,
            shipping_minor=499,
            tax_minor=245,
            currency="USD",
        )
        self.variant.price_minor = 3000
        self.variant.save()
        retry = create_order_snapshot(
            user,
            session,
            idempotency_key=idempotency_key,
            shipping_minor=499,
            tax_minor=245,
            currency="USD",
        )

        self.assertEqual(retry.pk, order.pk)
        self.assertEqual(order.subtotal_minor, 4900)
        self.assertEqual(order.total_minor, 5644)
        self.assertEqual(order.lines.get().unit_price_minor, 2450)
        self.assertEqual(order.lines.get().product_name, "Everyday Tote")
        self.assertEqual(session["cart_items"], {str(self.variant.pk): 2})

    def test_order_snapshot_rejects_invalid_server_quote_before_creating_order(self):
        user = get_user_model().objects.create_user("buyer@example.com", "passphrase-1234")
        self.client.post(reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 1})

        with self.assertRaises(ValueError):
            create_order_snapshot(
                user,
                self.client.session,
                idempotency_key=uuid.uuid4(),
                shipping_minor=-1,
                tax_minor=0,
                currency="USD",
            )

        self.assertEqual(Order.objects.count(), 0)

    def test_order_snapshot_rejects_reusing_key_with_changed_quote(self):
        user = get_user_model().objects.create_user("buyer@example.com", "passphrase-1234")
        self.client.post(reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 1})
        key = uuid.uuid4()
        create_order_snapshot(
            user,
            self.client.session,
            idempotency_key=key,
            shipping_minor=100,
            tax_minor=0,
            currency="USD",
        )

        with self.assertRaises(CartError):
            create_order_snapshot(
                user,
                self.client.session,
                idempotency_key=key,
                shipping_minor=200,
                tax_minor=0,
                currency="USD",
            )

        self.assertEqual(Order.objects.count(), 1)

    def test_checkout_does_not_create_an_order_or_clear_the_cart(self):
        self.client.post(reverse("commerce:add"), {"variant_id": self.variant.pk, "quantity": 1})

        response = self.client.get(reverse("commerce:checkout"))

        self.assertContains(
            response, "Shipping destinations, tax rules, and verified payment methods"
        )
        self.assertEqual(self.client.session["cart_items"], {str(self.variant.pk): 1})

    def test_cart_mutations_are_post_only(self):
        self.assertEqual(self.client.get(reverse("commerce:add")).status_code, 405)
        self.assertEqual(
            self.client.get(reverse("commerce:remove", args=[self.variant.pk])).status_code, 405
        )

    @override_settings(CART_MAX_QUANTITY=3)
    def test_session_cannot_inject_an_excessive_quantity(self):
        session = self.client.session
        session["cart_items"] = {str(self.variant.pk): "999999"}
        session.save()

        summary = cart_summary(self.client.session)

        self.assertEqual(summary.lines, [])
        self.assertFalse(summary.can_continue)


class OrderHistoryAccessTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner@example.com", "passphrase-1234")
        self.other_user = get_user_model().objects.create_user(
            "other@example.com", "passphrase-5678"
        )
        product = Product.objects.create(
            name="Desk Lamp",
            slug="desk-lamp",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        variant = ProductVariant.objects.create(
            product=product, sku="LAMP-BRASS", label="Brass", price_minor=6200, available_units=2
        )
        self.order = create_order_snapshot(
            self.owner,
            {"cart_items": {str(variant.pk): 1}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=0,
            tax_minor=0,
            currency="USD",
        )

    def test_order_history_is_available_to_signed_in_customer(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:orders"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.order_number.hex[:8])

    def test_customer_cannot_view_another_customers_order(self):
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("accounts:order-detail", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 404)

    def test_order_detail_displays_saved_price_after_catalog_changes(self):
        Product.objects.filter(slug="desk-lamp").update(name="Updated product title")
        ProductVariant.objects.filter(sku="LAMP-BRASS").update(price_minor=9999)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:order-detail", args=[self.order.order_number]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Desk Lamp")
        self.assertContains(response, "USD 62.00")
        self.assertNotContains(response, "Updated product title")

    def test_order_line_snapshot_cannot_be_saved_over(self):
        line = self.order.lines.get()
        line.product_name = "Changed title"

        with self.assertRaises(ValidationError):
            line.save()


class PaymentStateTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("payer@example.com", "passphrase-1234")
        product = Product.objects.create(
            name="Canvas Pouch",
            slug="canvas-pouch",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        variant = ProductVariant.objects.create(
            product=product, sku="POUCH-SAND", price_minor=1800, available_units=5
        )
        self.order = create_order_snapshot(
            user,
            {"cart_items": {str(variant.pk): 1}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=300,
            tax_minor=0,
            currency="USD",
        )
        self.attempt = create_payment_attempt(
            self.order, provider="stripe", idempotency_key=uuid.uuid4()
        )
        set_payment_attempt_reference(self.attempt, "cs_test_123")

    def make_event(self, **overrides):
        values = {
            "provider": "stripe",
            "event_id": "evt_checkout_123",
            "event_type": "checkout.session.completed",
            "attempt_key": self.attempt.idempotency_key,
            "provider_reference": "cs_test_123",
            "payload_digest": "a" * 64,
            "outcome": "paid",
            "amount_minor": self.order.total_minor,
            "currency": "USD",
            "transaction_reference": "pi_test_123",
        }
        values.update(overrides)
        return VerifiedPaymentEvent(**values)

    def test_payment_attempt_copies_order_amount_and_reuses_its_key(self):
        attempt = self.attempt

        retry = create_payment_attempt(
            self.order,
            provider="stripe",
            idempotency_key=attempt.idempotency_key,
        )

        self.assertEqual(retry.pk, attempt.pk)
        self.assertEqual(retry.amount_minor, self.order.total_minor)
        self.assertEqual(retry.currency, self.order.currency)

    def test_only_one_open_payment_attempt_can_exist_for_an_order(self):
        with self.assertRaises(PaymentError):
            create_payment_attempt(self.order, provider="paypal", idempotency_key=uuid.uuid4())

    def test_verified_success_marks_payment_paid_without_changing_fulfillment(self):
        outcome = process_verified_payment_event(self.make_event())
        self.order.refresh_from_db()
        self.attempt.refresh_from_db()

        self.assertEqual(outcome, "processed")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(self.order.fulfillment_status, Order.FulfillmentStatus.UNFULFILLED)
        self.assertEqual(self.attempt.status, "succeeded")

    def test_duplicate_verified_event_has_no_second_effect(self):
        event = self.make_event()
        first = process_verified_payment_event(event)
        second = process_verified_payment_event(event)

        self.assertEqual((first, second), ("processed", "duplicate"))
        self.assertEqual(self.attempt.events.count(), 1)

    def test_mismatched_amount_is_recorded_and_requires_manual_review(self):
        outcome = process_verified_payment_event(self.make_event(amount_minor=1))
        self.order.refresh_from_db()
        self.attempt.refresh_from_db()

        self.assertEqual(outcome, "rejected")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.NEEDS_REVIEW)
        self.assertEqual(self.attempt.status, "succeeded")
        self.assertEqual(self.attempt.events.get().status, "rejected")
        self.assertEqual(self.attempt.events.get().transaction_reference, "pi_test_123")

    def test_paid_mismatch_blocks_a_second_attempt_until_review(self):
        process_verified_payment_event(self.make_event(amount_minor=1))

        with self.assertRaises(PaymentError):
            create_payment_attempt(self.order, provider="paypal", idempotency_key=uuid.uuid4())

    def test_reused_transaction_reference_is_quarantined_without_unique_key_failure(self):
        process_verified_payment_event(self.make_event())
        variant = ProductVariant.objects.get(sku="POUCH-SAND")
        second_order = create_order_snapshot(
            self.order.user,
            {"cart_items": {str(variant.pk): 1}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=300,
            tax_minor=0,
            currency="USD",
        )
        second_attempt = create_payment_attempt(
            second_order, provider="stripe", idempotency_key=uuid.uuid4()
        )
        set_payment_attempt_reference(second_attempt, "cs_test_second")
        duplicate_capture = self.make_event(
            event_id="evt_second_capture",
            attempt_key=second_attempt.idempotency_key,
            provider_reference="cs_test_second",
        )

        outcome = process_verified_payment_event(duplicate_capture)

        second_order.refresh_from_db()
        second_attempt.refresh_from_db()
        self.assertEqual(outcome, "rejected")
        self.assertEqual(second_order.payment_status, Order.PaymentStatus.NEEDS_REVIEW)
        self.assertEqual(second_attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(second_attempt.transaction_reference, "")
        self.assertEqual(second_attempt.events.get().transaction_reference, "pi_test_123")

    def test_late_failure_event_does_not_regress_a_paid_order(self):
        process_verified_payment_event(self.make_event())
        outcome = process_verified_payment_event(
            self.make_event(event_id="evt_late_failure", outcome="failed")
        )
        self.order.refresh_from_db()

        self.assertEqual(outcome, "ignored")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    def test_late_pending_event_does_not_regress_a_paid_attempt(self):
        process_verified_payment_event(self.make_event())
        outcome = process_verified_payment_event(
            self.make_event(event_id="evt_late_pending", outcome="pending")
        )
        self.attempt.refresh_from_db()

        self.assertEqual(outcome, "ignored")
        self.assertEqual(self.attempt.status, "succeeded")

    def test_late_cancellation_does_not_regress_a_paid_attempt(self):
        process_verified_payment_event(self.make_event())
        outcome = process_verified_payment_event(
            self.make_event(event_id="evt_late_cancel", outcome="cancelled")
        )
        self.attempt.refresh_from_db()

        self.assertEqual(outcome, "ignored")
        self.assertEqual(self.attempt.status, "succeeded")

    def test_failed_attempt_can_be_retried_with_a_new_key(self):
        outcome = process_verified_payment_event(
            self.make_event(event_id="evt_failed", outcome="failed")
        )
        self.order.refresh_from_db()
        self.assertEqual(outcome, "processed")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.FAILED)

        retry = create_payment_attempt(self.order, provider="paypal", idempotency_key=uuid.uuid4())

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(retry.status, "created")
        self.assertNotEqual(retry.pk, self.attempt.pk)
