from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductVariant
from commerce.money import format_minor, major_to_minor, minor_to_major
from commerce.services import CartError, add_variant, cart_summary


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
