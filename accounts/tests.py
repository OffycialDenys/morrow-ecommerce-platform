import json
import uuid

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import SavedProduct, User
from catalog.models import Product, ProductVariant
from commerce.models import Order, OrderLine, PaymentAttempt
from promotions.models import PointEntry


class AccountFlowTests(TestCase):
    def test_registration_normalizes_email_and_starts_authenticated_session(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "email": "  Shopper@Example.com ",
                "password1": "A-reasonably-long-password-82!",
                "password2": "A-reasonably-long-password-82!",
            },
        )

        self.assertRedirects(response, reverse("storefront:account"))
        user = User.objects.get(email="shopper@example.com")
        self.assertTrue(user.check_password("A-reasonably-long-password-82!"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_email_login_is_case_insensitive(self):
        user = User.objects.create_user("shopper@example.com", "A-reasonably-long-password-82!")

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "SHOPPER@EXAMPLE.COM", "password": "A-reasonably-long-password-82!"},
        )

        self.assertRedirects(response, reverse("storefront:account"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_case_insensitive_duplicate_email_is_rejected(self):
        User.objects.create_user("shopper@example.com", "A-reasonably-long-password-82!")

        response = self.client.post(
            reverse("accounts:register"),
            {
                "email": "SHOPPER@example.com",
                "password1": "A-different-long-password-83!",
                "password2": "A-different-long-password-83!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.count(), 1)

    def test_registration_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            reverse("accounts:register"),
            {
                "email": "shopper@example.com",
                "password1": "A-reasonably-long-password-82!",
                "password2": "A-reasonably-long-password-82!",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 0)

    def test_logout_is_post_only(self):
        user = User.objects.create_user("shopper@example.com", "A-reasonably-long-password-82!")
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("storefront:home"))
        self.assertNotIn("_auth_user_id", self.client.session)


class SavedProductAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "A-reasonably-long-password-82!")
        self.other = User.objects.create_user("other@example.com", "A-reasonably-long-password-82!")
        self.product = Product.objects.create(
            name="Field Notes", slug="field-notes", currency="USD", publication_status="published"
        )
        ProductVariant.objects.create(
            product=self.product, sku="FIELD-NOTES", price_minor=1200, available_units=5
        )
        SavedProduct.objects.create(user=self.owner, product=self.product)

    def test_saved_page_only_shows_current_users_items(self):
        self.client.force_login(self.other)

        response = self.client.get(reverse("accounts:saved"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.product.name)

    def test_customer_can_only_toggle_own_saved_product(self):
        self.client.force_login(self.other)

        response = self.client.post(reverse("accounts:toggle-saved", args=[self.product.pk]))

        self.assertRedirects(response, reverse("storefront:shop"))
        self.assertTrue(SavedProduct.objects.filter(user=self.owner, product=self.product).exists())
        self.assertTrue(SavedProduct.objects.filter(user=self.other, product=self.product).exists())

    def test_saved_product_return_url_cannot_redirect_off_site(self):
        self.client.force_login(self.other)

        response = self.client.post(
            reverse("accounts:toggle-saved", args=[self.product.pk]),
            {"next": "https://attacker.example/"},
        )

        self.assertRedirects(response, reverse("storefront:shop"))

    def test_anonymous_customer_cannot_toggle_saved_items(self):
        response = self.client.post(reverse("accounts:toggle-saved", args=[self.product.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.assertEqual(SavedProduct.objects.count(), 1)


class AccountDataExportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "A-reasonably-long-password-82!")
        self.other = User.objects.create_user("other@example.com", "A-reasonably-long-password-82!")
        self.product = Product.objects.create(
            name="Field Notes", slug="field-notes", currency="USD", publication_status="published"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="FIELD-NOTES", price_minor=1200, available_units=5
        )
        SavedProduct.objects.create(user=self.owner, product=self.product)
        self.order = Order.objects.create(
            idempotency_key=uuid.uuid4(),
            request_fingerprint="a" * 64,
            user=self.owner,
            buyer_email=self.owner.email,
            currency="USD",
            subtotal_minor=1200,
            shipping_minor=0,
            tax_minor=0,
            total_minor=1200,
        )
        self.line = OrderLine.objects.create(
            order=self.order,
            variant=self.variant,
            product_name=self.product.name,
            sku=self.variant.sku,
            quantity=1,
            unit_price_minor=1200,
            line_total_minor=1200,
        )
        PaymentAttempt.objects.create(
            order=self.order,
            provider=PaymentAttempt.Provider.STRIPE,
            idempotency_key=uuid.uuid4(),
            amount_minor=1200,
            currency="USD",
        )
        PointEntry.objects.create(
            customer=self.owner,
            order=self.order,
            order_line=self.line,
            event_type=PointEntry.EventType.PURCHASE,
            points=5,
            event_key="owner-order-points",
            reason="Qualifying purchase",
        )

    def test_export_requires_authentication(self):
        response = self.client.get(reverse("accounts:data-export"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:data-export')}",
        )

    def test_export_contains_owner_records_without_credentials_or_other_accounts(self):
        other_product = Product.objects.create(
            name="Other customer item",
            slug="other-customer-item",
            currency="USD",
            publication_status="published",
        )
        SavedProduct.objects.create(user=self.other, product=other_product)
        other_order = Order.objects.create(
            idempotency_key=uuid.uuid4(),
            request_fingerprint="b" * 64,
            user=self.other,
            buyer_email=self.other.email,
            currency="USD",
            subtotal_minor=1200,
            shipping_minor=0,
            tax_minor=0,
            total_minor=1200,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:data-export"))
        payload = json.loads(b"".join(response.streaming_content))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json; charset=utf-8")
        self.assertIn('attachment; filename="morrow-account-data-', response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["account"]["email"], self.owner.email)
        self.assertNotIn("password", payload["account"])
        self.assertEqual(payload["saved_products"][0]["product_name"], self.product.name)
        self.assertEqual(len(payload["orders"]), 1)
        self.assertNotIn(str(other_order.order_number), json.dumps(payload))
        self.assertEqual(payload["orders"][0]["items"][0]["sku"], self.variant.sku)
        self.assertEqual(payload["orders"][0]["payment_attempts"][0]["provider"], "stripe")
        self.assertEqual(payload["points_ledger"][0]["points"], 5)
        self.assertNotIn("Other customer item", json.dumps(payload))
