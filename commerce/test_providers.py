import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductVariant
from commerce.models import Order, PaymentAttempt
from commerce.payments import create_payment_attempt, set_payment_attempt_reference
from commerce.providers import PayPalGateway, StripeGateway
from commerce.services import create_order_snapshot


class ProviderAdapterTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("provider@example.com", "safe-passphrase")
        product = Product.objects.create(
            name="Woven Market Bag",
            slug="woven-market-bag",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        variant = ProductVariant.objects.create(
            product=product,
            sku="BAG-NATURAL",
            label="Natural",
            price_minor=1800,
            available_units=3,
        )
        self.order = create_order_snapshot(
            user,
            {"cart_items": {str(variant.pk): 2}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=350,
            tax_minor=250,
            currency="USD",
        )
        self.attempt = create_payment_attempt(
            self.order,
            provider=PaymentAttempt.Provider.STRIPE,
            idempotency_key=uuid.uuid4(),
        )

    def test_stripe_checkout_uses_order_snapshot_and_stable_idempotency_key(self):
        calls = []

        def create(params, options):
            calls.append((params, options))
            return SimpleNamespace(
                id="cs_test_123", url="https://checkout.stripe.com/c/pay/cs_test_123"
            )

        sessions = SimpleNamespace(create=create)
        client = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=sessions)))
        gateway = StripeGateway(secret_key="sk_test_example", client=client)

        url = gateway.create_checkout_session(
            self.attempt,
            success_url="http://localhost/checkout/success/",
            cancel_url="http://localhost/cart/",
        )

        params, options = calls[0]
        self.assertEqual(url, "https://checkout.stripe.com/c/pay/cs_test_123")
        self.assertEqual(options["idempotency_key"], str(self.attempt.idempotency_key))
        self.assertEqual(params["line_items"][0]["price_data"]["unit_amount"], 1800)
        self.assertEqual(params["line_items"][0]["quantity"], 2)
        self.assertEqual(params["line_items"][1]["price_data"]["unit_amount"], 350)
        self.assertEqual(params["line_items"][2]["price_data"]["unit_amount"], 250)
        self.assertEqual(params["metadata"]["order_number"], str(self.order.order_number))
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.provider_reference, "cs_test_123")

    def test_stripe_rejects_a_provider_redirect_outside_its_checkout_host(self):
        sessions = SimpleNamespace(
            create=lambda params, options: SimpleNamespace(
                id="cs_test_123", url="https://attacker.example/checkout"
            )
        )
        client = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=sessions)))

        with self.assertRaisesMessage(ValueError, "unapproved checkout URL"):
            StripeGateway(secret_key="sk_test_example", client=client).create_checkout_session(
                self.attempt,
                success_url="http://localhost/checkout/success/",
                cancel_url="http://localhost/cart/",
            )

    def test_stripe_webhook_requires_a_valid_provider_signature(self):
        raw_body = json.dumps(
            {
                "id": "evt_checkout_1",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_123",
                        "amount_total": self.order.total_minor,
                        "currency": "usd",
                        "payment_status": "paid",
                        "payment_intent": "pi_test_123",
                        "metadata": {"payment_attempt_key": str(self.attempt.idempotency_key)},
                    }
                },
            }
        ).encode()
        secret = "whsec_provider_test"
        signature = stripe.WebhookSignature.generate_signature_header(
            raw_body.decode(), secret, timestamp=int(time.time())
        )
        gateway = StripeGateway(
            secret_key="sk_test_example", webhook_secret=secret, client=object()
        )

        event = gateway.verify_webhook(raw_body, signature)

        self.assertEqual(event.event_id, "evt_checkout_1")
        self.assertEqual(event.attempt_key, self.attempt.idempotency_key)
        self.assertEqual(event.amount_minor, self.order.total_minor)
        with self.assertRaises(ValueError):
            gateway.verify_webhook(raw_body, "t=1,v1=invalid")

    def test_paypal_checkout_uses_exact_order_totals_and_request_id(self):
        self.attempt.status = PaymentAttempt.Status.FAILED
        self.attempt.save(update_fields=("status",))
        calls = []

        def create_order(options):
            calls.append(options)
            return SimpleNamespace(
                body=SimpleNamespace(
                    id="PAYPAL-ORDER-123",
                    links=[
                        {
                            "rel": "approve",
                            "href": "https://www.sandbox.paypal.com/checkoutnow?token=PAYPAL-ORDER-123",
                        }
                    ],
                )
            )

        client = SimpleNamespace(orders=SimpleNamespace(create_order=create_order))
        attempt = create_payment_attempt(
            self.order,
            provider=PaymentAttempt.Provider.PAYPAL,
            idempotency_key=uuid.uuid4(),
        )

        url = PayPalGateway(client=client).create_order(
            attempt,
            return_url="http://localhost/payments/return/",
            cancel_url="http://localhost/cart/",
        )

        self.assertEqual(url, "https://www.sandbox.paypal.com/checkoutnow?token=PAYPAL-ORDER-123")
        self.assertEqual(calls[0]["paypal_request_id"], attempt.idempotency_key.hex[:25])
        purchase_unit = calls[0]["body"].purchase_units[0]
        self.assertEqual(purchase_unit.amount.value, "42.00")
        self.assertEqual(purchase_unit.amount.breakdown.item_total.value, "36.00")
        self.assertEqual(purchase_unit.amount.breakdown.shipping.value, "3.50")
        self.assertEqual(purchase_unit.amount.breakdown.tax_total.value, "2.50")
        self.assertEqual(purchase_unit.items[0].quantity, "2")
        self.assertEqual(purchase_unit.custom_id, attempt.idempotency_key.hex)

    def test_paypal_webhook_amount_is_converted_without_float_rounding(self):
        self.attempt.status = PaymentAttempt.Status.FAILED
        self.attempt.save(update_fields=("status",))
        attempt = create_payment_attempt(
            self.order,
            provider=PaymentAttempt.Provider.PAYPAL,
            idempotency_key=uuid.uuid4(),
        )
        set_payment_attempt_reference(attempt, "PAYPAL-ORDER-123")
        raw_body = b'{"id":"WH-1","event_type":"PAYMENT.CAPTURE.COMPLETED"}'
        event = {
            "id": "WH-1",
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {
                "id": "CAPTURE-1",
                "amount": {"currency_code": "USD", "value": "42.00"},
                "supplementary_data": {"related_ids": {"order_id": "PAYPAL-ORDER-123"}},
            },
        }

        normalized = PayPalGateway(client=object()).normalize_webhook(event, raw_body)

        self.assertEqual(normalized.amount_minor, self.order.total_minor)
        self.assertEqual(normalized.attempt_key, attempt.idempotency_key)
        self.assertEqual(normalized.provider_reference, "PAYPAL-ORDER-123")
        self.assertEqual(normalized.transaction_reference, "CAPTURE-1")

    def test_paypal_capture_uses_a_stable_request_id_and_records_capture_reference(self):
        self.attempt.status = PaymentAttempt.Status.FAILED
        self.attempt.save(update_fields=("status",))
        attempt = create_payment_attempt(
            self.order,
            provider=PaymentAttempt.Provider.PAYPAL,
            idempotency_key=uuid.uuid4(),
        )
        set_payment_attempt_reference(attempt, "PAYPAL-ORDER-123")
        requests = []

        def capture_order(options):
            requests.append(options)
            return SimpleNamespace(
                body=SimpleNamespace(
                    id="PAYPAL-ORDER-123",
                    purchase_units=[
                        SimpleNamespace(
                            payments=SimpleNamespace(
                                captures=[
                                    SimpleNamespace(
                                        id="CAPTURE-123",
                                        status="COMPLETED",
                                        amount=SimpleNamespace(currency_code="USD", value="42.00"),
                                    )
                                ]
                            )
                        )
                    ],
                )
            )

        gateway = PayPalGateway(
            client=SimpleNamespace(orders=SimpleNamespace(capture_order=capture_order))
        )

        result = gateway.capture_order(attempt)

        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(result, "processed")
        self.assertEqual(requests[0]["id"], "PAYPAL-ORDER-123")
        self.assertEqual(
            requests[0]["paypal_request_id"], f"{attempt.idempotency_key.hex[:24]}-capture"
        )
        self.assertEqual(attempt.transaction_reference, "CAPTURE-123")
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    @override_settings(
        PAYPAL_CLIENT_ID="client-id",
        PAYPAL_CLIENT_SECRET="client-secret",
        PAYPAL_WEBHOOK_ID="WH-STORE-1",
    )
    @patch("commerce.providers.requests.post")
    def test_paypal_verification_preserves_the_signed_event_body(self, post):
        raw_body = b'{"id":"WH-1", "event_type":"PAYMENT.CAPTURE.COMPLETED"}'
        post.side_effect = [
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"access_token": "access-token"},
            ),
            SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"verification_status": "SUCCESS"},
            ),
        ]
        headers = {
            "paypal-auth-algo": "SHA256withRSA",
            "paypal-cert-url": "https://api-m.sandbox.paypal.com/v1/notifications/certs/CERT-1",
            "paypal-transmission-id": "TRANS-1",
            "paypal-transmission-sig": "signature",
            "paypal-transmission-time": "2026-10-21T00:00:00Z",
        }

        event = PayPalGateway(client=object()).verify_webhook(raw_body, headers)

        self.assertEqual(event["id"], "WH-1")
        verification_body = post.call_args_list[1].kwargs["data"]
        self.assertTrue(verification_body.endswith(b',"webhook_event":' + raw_body + b"}"))

    def test_verified_stripe_webhook_endpoint_applies_payment_event(self):
        self.attempt.provider_reference = "cs_test_123"
        self.attempt.save(update_fields=("provider_reference",))
        raw_body = json.dumps(
            {
                "id": "evt_http_1",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_123",
                        "amount_total": self.order.total_minor,
                        "currency": "usd",
                        "payment_status": "paid",
                        "payment_intent": "pi_http_1",
                        "metadata": {"payment_attempt_key": str(self.attempt.idempotency_key)},
                    }
                },
            }
        ).encode()
        secret = "whsec_provider_test"
        signature = stripe.WebhookSignature.generate_signature_header(
            raw_body.decode(), secret, timestamp=int(time.time())
        )

        with override_settings(STRIPE_SECRET_KEY="sk_test_example", STRIPE_WEBHOOK_SECRET=secret):
            response = self.client.post(
                reverse("commerce:stripe-webhook"),
                data=raw_body,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=signature,
            )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)

    @override_settings(STRIPE_SECRET_KEY="sk_test_example", STRIPE_WEBHOOK_SECRET="whsec_test")
    def test_stripe_webhook_endpoint_rejects_an_invalid_signature(self):
        response = self.client.post(
            reverse("commerce:stripe-webhook"),
            data=b'{"id":"evt_bad","type":"checkout.session.completed"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=invalid",
        )

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)

    @override_settings(
        PAYMENT_ENVIRONMENT="sandbox",
        PAYPAL_CLIENT_ID="client-id",
        PAYPAL_CLIENT_SECRET="client-secret",
        PAYPAL_WEBHOOK_ID="WH-STORE-1",
    )
    @patch.object(PayPalGateway, "verify_webhook")
    def test_paypal_webhook_endpoint_processes_a_verified_capture(self, verify_webhook):
        self.attempt.status = PaymentAttempt.Status.FAILED
        self.attempt.save(update_fields=("status",))
        attempt = create_payment_attempt(
            self.order,
            provider=PaymentAttempt.Provider.PAYPAL,
            idempotency_key=uuid.uuid4(),
        )
        set_payment_attempt_reference(attempt, "PAYPAL-ORDER-HTTP")
        raw_body = b'{"id":"WH-HTTP-1","event_type":"PAYMENT.CAPTURE.COMPLETED"}'
        verify_webhook.return_value = {
            "id": "WH-HTTP-1",
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {
                "id": "CAPTURE-HTTP-1",
                "amount": {"currency_code": "USD", "value": "42.00"},
                "supplementary_data": {"related_ids": {"order_id": "PAYPAL-ORDER-HTTP"}},
            },
        }

        response = self.client.post(
            reverse("commerce:paypal-webhook"), data=raw_body, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        verify_webhook.assert_called_once()
        self.order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(attempt.transaction_reference, "CAPTURE-HTTP-1")

    def test_webhook_body_size_is_limited_before_processing(self):
        response = self.client.post(
            reverse("commerce:stripe-webhook"),
            data=b"x" * (1_048_576 + 1),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 413)
