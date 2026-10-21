"""Stripe and PayPal adapters; no provider data is trusted before verification."""

import hashlib
import json
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from uuid import UUID

import requests
import stripe
from django.conf import settings
from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.amount_breakdown import AmountBreakdown
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.item_request import ItemRequest
from paypalserversdk.models.money import Money
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.payment_source import PaymentSource
from paypalserversdk.models.paypal_wallet import PaypalWallet
from paypalserversdk.models.paypal_wallet_experience_context import PaypalWalletExperienceContext
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient

from .models import Order, PaymentAttempt
from .money import major_to_minor, minor_to_major
from .payments import (
    PaymentError,
    VerifiedPaymentEvent,
    process_verified_payment_event,
    set_payment_attempt_reference,
)

logger = logging.getLogger(__name__)
MAX_WEBHOOK_BODY_BYTES = 1_048_576


class ProviderConfigurationError(PaymentError):
    """A provider cannot be used with the current server configuration."""


class ProviderRequestError(PaymentError):
    """A provider request could not be completed safely."""


class ProviderVerificationError(PaymentError):
    """A webhook did not pass provider authenticity checks."""


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _host_is_allowed(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().rstrip(".")
    for allowed in settings.ALLOWED_HOSTS:
        allowed = allowed.lower().rstrip(".")
        if (
            allowed == "*"
            or host == allowed
            or (allowed.startswith(".") and host.endswith(allowed))
        ):
            return True
    return False


def _validate_return_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise PaymentError("Payment return URLs must use an approved store origin.") from error
    expected_scheme = "https" if settings.PAYMENT_ENVIRONMENT == "production" else {"http", "https"}
    allowed_scheme = (
        parsed.scheme == expected_scheme
        if isinstance(expected_scheme, str)
        else parsed.scheme in expected_scheme
    )
    if (
        not allowed_scheme
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not _host_is_allowed(parsed.hostname)
    ):
        raise PaymentError("Payment return URLs must use an approved store origin.")
    return value


def _is_provider_https_url(value: str, hostname: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == hostname
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
    )


def _order_lines(order: Order):
    lines = list(order.lines.order_by("pk"))
    if not lines or sum(line.line_total_minor for line in lines) != order.subtotal_minor:
        raise PaymentError("The order snapshot is incomplete or inconsistent.")
    if order.total_minor != order.subtotal_minor + order.shipping_minor + order.tax_minor:
        raise PaymentError("The order total does not match its saved charges.")
    return lines


def _stripe_key_matches_environment(key: str) -> bool:
    if settings.PAYMENT_ENVIRONMENT == "sandbox":
        return key.startswith(("sk_test_", "rk_test_"))
    return key.startswith(("sk_live_", "rk_live_"))


class StripeGateway:
    def __init__(
        self, *, secret_key: str | None = None, webhook_secret: str | None = None, client=None
    ):
        self.secret_key = secret_key if secret_key is not None else settings.STRIPE_SECRET_KEY
        self.webhook_secret = (
            webhook_secret if webhook_secret is not None else settings.STRIPE_WEBHOOK_SECRET
        )
        if not self.secret_key:
            raise ProviderConfigurationError("Stripe is not configured on this store.")
        if not _stripe_key_matches_environment(self.secret_key):
            raise ProviderConfigurationError(
                "The Stripe key does not match the selected environment."
            )
        self.client = client or stripe.StripeClient(self.secret_key, max_network_retries=2)

    def create_checkout_session(
        self,
        attempt: PaymentAttempt,
        *,
        success_url: str,
        cancel_url: str,
    ) -> str:
        if attempt.provider != PaymentAttempt.Provider.STRIPE:
            raise PaymentError("This payment attempt belongs to another provider.")
        success_url = _validate_return_url(success_url)
        cancel_url = _validate_return_url(cancel_url)
        order = attempt.order
        lines = _order_lines(order)
        if order.currency != attempt.currency or order.total_minor != attempt.amount_minor:
            raise PaymentError("The payment attempt does not match its saved order amount.")

        currency = order.currency.lower()
        line_items = [
            {
                "price_data": {
                    "currency": currency,
                    "unit_amount": line.unit_price_minor,
                    "product_data": {"name": line.product_name[:127]},
                },
                "quantity": line.quantity,
            }
            for line in lines
        ]
        if order.shipping_minor:
            line_items.append(
                {
                    "price_data": {
                        "currency": currency,
                        "unit_amount": order.shipping_minor,
                        "product_data": {"name": "Shipping"},
                    },
                    "quantity": 1,
                }
            )
        if order.tax_minor:
            line_items.append(
                {
                    "price_data": {
                        "currency": currency,
                        "unit_amount": order.tax_minor,
                        "product_data": {"name": "Tax"},
                    },
                    "quantity": 1,
                }
            )
        if len(line_items) > 100:
            raise PaymentError("This order exceeds Stripe Checkout's line-item limit.")

        metadata = {
            "order_number": str(order.order_number),
            "payment_attempt_key": str(attempt.idempotency_key),
        }
        params = {
            "mode": "payment",
            "client_reference_id": str(order.order_number),
            "customer_email": order.buyer_email,
            "line_items": line_items,
            "metadata": metadata,
            "payment_intent_data": {"metadata": metadata},
            "success_url": success_url,
            "cancel_url": cancel_url,
        }

        try:
            session = self.client.v1.checkout.sessions.create(
                params,
                options={"idempotency_key": str(attempt.idempotency_key)},
            )
        except stripe.StripeError as error:
            logger.warning("Stripe Checkout request failed for order %s", order.order_number)
            raise ProviderRequestError("The payment provider could not start checkout.") from error

        reference = _field(session, "id")
        url = _field(session, "url")
        if not isinstance(reference, str) or not reference or not isinstance(url, str):
            raise ProviderRequestError("Stripe returned an incomplete checkout response.")
        if not _is_provider_https_url(url, "checkout.stripe.com"):
            raise ProviderRequestError("Stripe returned an unapproved checkout URL.")
        set_payment_attempt_reference(attempt, reference)
        return url

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> VerifiedPaymentEvent | None:
        if not self.webhook_secret:
            raise ProviderConfigurationError("Stripe webhook verification is not configured.")
        if len(raw_body) > MAX_WEBHOOK_BODY_BYTES or not signature:
            raise ProviderVerificationError("The Stripe webhook is invalid.")
        try:
            provider_event = stripe.Webhook.construct_event(
                raw_body, signature, self.webhook_secret
            )
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise ProviderVerificationError("The Stripe webhook signature is invalid.") from error

        event_type = _field(provider_event, "type", "")
        event_id = _field(provider_event, "id", "")
        if not isinstance(event_type, str) or not isinstance(event_id, str) or not event_id:
            raise ProviderVerificationError("The Stripe event identity is invalid.")
        outcomes = {
            "checkout.session.completed": "pending",
            "checkout.session.async_payment_succeeded": "paid",
            "checkout.session.async_payment_failed": "failed",
            "checkout.session.expired": "cancelled",
        }
        if event_type not in outcomes:
            return None

        session = _field(_field(provider_event, "data", {}), "object", {})
        session_reference = _field(session, "id")
        if not isinstance(session_reference, str) or not session_reference:
            raise ProviderVerificationError("The Stripe event is missing its checkout reference.")
        if event_type == "checkout.session.completed":
            outcome = "paid" if _field(session, "payment_status") == "paid" else "pending"
        else:
            outcome = outcomes[event_type]
        amount = _field(session, "amount_total")
        currency = _field(session, "currency")
        if type(amount) is not int or not isinstance(currency, str):  # noqa: E721
            raise ProviderVerificationError("The Stripe event is missing its amount.")
        metadata = _field(session, "metadata", {}) or {}
        raw_attempt_key = _field(metadata, "payment_attempt_key")
        try:
            attempt_key = UUID(str(raw_attempt_key)) if raw_attempt_key else None
        except ValueError:
            attempt_key = None
        payment_intent = _field(session, "payment_intent")
        transaction_reference = (
            payment_intent
            if isinstance(payment_intent, str)
            else str(_field(payment_intent, "id", "") or "")
        )
        if outcome == "paid" and not transaction_reference:
            raise ProviderVerificationError("The Stripe event is missing its payment reference.")
        return VerifiedPaymentEvent(
            provider=PaymentAttempt.Provider.STRIPE,
            event_id=event_id,
            event_type=event_type,
            attempt_key=attempt_key,
            provider_reference=session_reference,
            payload_digest=hashlib.sha256(raw_body).hexdigest(),
            outcome=outcome,
            amount_minor=amount,
            currency=currency.upper(),
            transaction_reference=transaction_reference,
        )


def _paypal_environment():
    return (
        Environment.SANDBOX if settings.PAYMENT_ENVIRONMENT == "sandbox" else Environment.PRODUCTION
    )


def _paypal_api_origin() -> str:
    return (
        "https://api-m.sandbox.paypal.com"
        if settings.PAYMENT_ENVIRONMENT == "sandbox"
        else "https://api-m.paypal.com"
    )


def _money_string(amount_minor: int, currency: str) -> str:
    return format(minor_to_major(amount_minor, currency), "f")


class PayPalGateway:
    def __init__(
        self, *, client_id: str | None = None, client_secret: str | None = None, client=None
    ):
        self.client_id = client_id if client_id is not None else settings.PAYPAL_CLIENT_ID
        self.client_secret = (
            client_secret if client_secret is not None else settings.PAYPAL_CLIENT_SECRET
        )
        if client is not None:
            self.client = client
        else:
            if not self.client_id or not self.client_secret:
                raise ProviderConfigurationError("PayPal is not configured on this store.")
            credentials = ClientCredentialsAuthCredentials(
                o_auth_client_id=self.client_id,
                o_auth_client_secret=self.client_secret,
            )
            self.client = PaypalServersdkClient(
                client_credentials_auth_credentials=credentials,
                environment=_paypal_environment(),
                timeout=10,
                max_retries=0,
            )

    def create_order(
        self,
        attempt: PaymentAttempt,
        *,
        return_url: str,
        cancel_url: str,
    ) -> str:
        if attempt.provider != PaymentAttempt.Provider.PAYPAL:
            raise PaymentError("This payment attempt belongs to another provider.")
        return_url = _validate_return_url(return_url)
        cancel_url = _validate_return_url(cancel_url)
        order = attempt.order
        lines = _order_lines(order)
        if order.currency != attempt.currency or order.total_minor != attempt.amount_minor:
            raise PaymentError("The payment attempt does not match its saved order amount.")
        if len(lines) > 100:
            raise PaymentError("This order exceeds PayPal's item limit.")

        currency = order.currency
        item_total = Money(
            currency_code=currency,
            value=_money_string(order.subtotal_minor, currency),
        )
        breakdown = AmountBreakdown(
            item_total=item_total,
            shipping=(
                Money(currency_code=currency, value=_money_string(order.shipping_minor, currency))
                if order.shipping_minor
                else None
            ),
            tax_total=(
                Money(currency_code=currency, value=_money_string(order.tax_minor, currency))
                if order.tax_minor
                else None
            ),
        )
        items = [
            ItemRequest(
                name=(
                    line.product_name + (f" — {line.variant_label}" if line.variant_label else "")
                )[:127],
                sku=line.sku[:127],
                quantity=str(line.quantity),
                unit_amount=Money(
                    currency_code=currency,
                    value=_money_string(line.unit_price_minor, currency),
                ),
            )
            for line in lines
        ]
        experience = PaypalWalletExperienceContext(
            return_url=return_url,
            cancel_url=cancel_url,
            user_action="PAY_NOW",
            shipping_preference="GET_FROM_FILE",
        )
        body = OrderRequest(
            intent=CheckoutPaymentIntent.CAPTURE,
            payment_source=PaymentSource(paypal=PaypalWallet(experience_context=experience)),
            purchase_units=[
                PurchaseUnitRequest(
                    reference_id="morrow-order",
                    custom_id=attempt.idempotency_key.hex,
                    invoice_id=order.order_number.hex,
                    description=f"Morrow order {order.order_number.hex[:12]}",
                    amount=AmountWithBreakdown(
                        currency_code=currency,
                        value=_money_string(order.total_minor, currency),
                        breakdown=breakdown,
                    ),
                    items=items,
                )
            ],
        )
        request_id = attempt.idempotency_key.hex[:25]
        try:
            response = self.client.orders.create_order(
                {"body": body, "paypal_request_id": request_id, "prefer": "return=representation"}
            )
        except Exception as error:
            logger.warning("PayPal order request failed for order %s", order.order_number)
            raise ProviderRequestError("The payment provider could not start checkout.") from error
        result = _field(response, "body")
        reference = _field(result, "id")
        links = _field(result, "links", []) or []
        approval = next(
            (
                _field(link, "href")
                for link in links
                if _field(link, "rel") in {"approve", "payer-action"}
            ),
            None,
        )
        if not isinstance(reference, str) or not isinstance(approval, str):
            raise ProviderRequestError("PayPal returned an incomplete checkout response.")
        allowed_host = (
            "www.sandbox.paypal.com"
            if settings.PAYMENT_ENVIRONMENT == "sandbox"
            else "www.paypal.com"
        )
        if not _is_provider_https_url(approval, allowed_host):
            raise ProviderRequestError("PayPal returned an unapproved checkout URL.")
        set_payment_attempt_reference(attempt, reference)
        return approval

    def capture_order(self, attempt: PaymentAttempt) -> str:
        if attempt.provider != PaymentAttempt.Provider.PAYPAL:
            raise PaymentError("This payment attempt belongs to another provider.")
        attempt.refresh_from_db()
        if (
            attempt.status != PaymentAttempt.Status.AWAITING_CUSTOMER
            or not attempt.provider_reference
        ):
            raise PaymentError("This PayPal order is not ready to capture.")
        request_id = f"{attempt.idempotency_key.hex[:24]}-capture"
        try:
            response = self.client.orders.capture_order(
                {
                    "id": attempt.provider_reference,
                    "paypal_request_id": request_id,
                    "prefer": "return=representation",
                }
            )
        except Exception as error:
            logger.warning("PayPal capture request failed for order %s", attempt.order_id)
            raise ProviderRequestError(
                "The payment provider could not capture this order."
            ) from error

        result = _field(response, "body")
        if _field(result, "id") != attempt.provider_reference:
            raise ProviderRequestError("PayPal returned a capture for a different order.")
        units = _field(result, "purchase_units", []) or []
        captures = []
        for unit in units:
            payments = _field(unit, "payments", {}) or {}
            captures.extend(_field(payments, "captures", []) or [])
        if len(captures) != 1:
            raise ProviderRequestError("PayPal returned an incomplete capture response.")

        capture = captures[0]
        capture_id = _field(capture, "id")
        capture_status = _field(capture, "status")
        amount = _field(capture, "amount", {}) or {}
        try:
            currency = _field(amount, "currency_code")
            amount_value = Decimal(str(_field(amount, "value")))
            if not amount_value.is_finite():
                raise ValueError("PayPal amount must be finite.")
            amount_minor = major_to_minor(amount_value, currency)
        except (TypeError, ValueError, InvalidOperation, ArithmeticError, LookupError) as error:
            raise ProviderRequestError("PayPal returned an invalid capture amount.") from error
        outcome_by_status = {"COMPLETED": "paid", "PENDING": "pending", "DECLINED": "failed"}
        outcome = outcome_by_status.get(capture_status)
        if not isinstance(capture_id, str) or not capture_id or outcome is None:
            raise ProviderRequestError("PayPal returned an unsupported capture status.")

        normalized_capture = {
            "order_id": attempt.provider_reference,
            "capture_id": capture_id,
            "status": capture_status,
            "amount": {"currency_code": currency, "value": str(_field(amount, "value"))},
        }
        event = VerifiedPaymentEvent(
            provider=PaymentAttempt.Provider.PAYPAL,
            event_id=capture_id,
            event_type=f"PAYMENT.CAPTURE.{capture_status}",
            attempt_key=attempt.idempotency_key,
            provider_reference=attempt.provider_reference,
            transaction_reference=capture_id,
            payload_digest=hashlib.sha256(
                json.dumps(normalized_capture, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            outcome=outcome,
            amount_minor=amount_minor,
            currency=str(currency).upper(),
        )
        return process_verified_payment_event(event)

    def verify_webhook(self, raw_body: bytes, headers) -> dict:
        if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
            raise ProviderConfigurationError("PayPal webhook verification is not configured.")
        webhook_id = settings.PAYPAL_WEBHOOK_ID
        if not webhook_id:
            raise ProviderConfigurationError("The PayPal webhook ID is not configured.")
        if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
            raise ProviderVerificationError("The PayPal webhook is too large.")

        normalized_headers = {key.lower(): value for key, value in headers.items()}
        auth_algo = normalized_headers.get("paypal-auth-algo", "")
        cert_url = normalized_headers.get("paypal-cert-url", "")
        transmission_id = normalized_headers.get("paypal-transmission-id", "")
        transmission_signature = normalized_headers.get("paypal-transmission-sig", "")
        transmission_time = normalized_headers.get("paypal-transmission-time", "")
        try:
            parsed_cert_url = urlsplit(cert_url)
            cert_port = parsed_cert_url.port
        except ValueError as error:
            raise ProviderVerificationError("The PayPal certificate URL is invalid.") from error
        expected_host = urlsplit(_paypal_api_origin()).hostname
        if (
            auth_algo != "SHA256withRSA"
            or not transmission_id
            or not transmission_signature
            or not transmission_time
            or parsed_cert_url.scheme != "https"
            or parsed_cert_url.hostname != expected_host
            or cert_port not in (None, 443)
            or not parsed_cert_url.path.startswith("/v1/notifications/certs/")
            or parsed_cert_url.username
            or parsed_cert_url.password
            or parsed_cert_url.query
            or parsed_cert_url.fragment
        ):
            raise ProviderVerificationError("The PayPal webhook headers are invalid.")
        try:
            event = json.loads(raw_body)
        except (ValueError, TypeError) as error:
            raise ProviderVerificationError("The PayPal webhook body is invalid.") from error
        if not isinstance(event, dict):
            raise ProviderVerificationError("The PayPal webhook body is invalid.")

        origin = _paypal_api_origin()
        timeout = (3, 8)
        try:
            token_response = requests.post(
                f"{origin}/v1/oauth2/token",
                auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
                timeout=timeout,
                allow_redirects=False,
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = (
                token_payload.get("access_token") if isinstance(token_payload, dict) else None
            )
            if not access_token:
                raise ProviderRequestError("PayPal did not return an access token.")

            envelope = {
                "auth_algo": auth_algo,
                "cert_url": cert_url,
                "transmission_id": transmission_id,
                "transmission_sig": transmission_signature,
                "transmission_time": transmission_time,
                "webhook_id": webhook_id,
            }
            prefix = ",".join(
                f"{json.dumps(key)}:{json.dumps(value)}" for key, value in envelope.items()
            ).encode()
            verification_body = b"{" + prefix + b',"webhook_event":' + raw_body + b"}"
            verification_response = requests.post(
                f"{origin}/v1/notifications/verify-webhook-signature",
                data=verification_body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
                allow_redirects=False,
            )
            verification_response.raise_for_status()
            result = verification_response.json()
        except ProviderRequestError:
            raise
        except (requests.RequestException, ValueError) as error:
            logger.warning("PayPal webhook verification service is unavailable")
            raise ProviderRequestError(
                "PayPal webhook verification is temporarily unavailable."
            ) from error
        if not isinstance(result, dict) or result.get("verification_status") != "SUCCESS":
            raise ProviderVerificationError("The PayPal webhook signature is invalid.")
        return event

    def normalize_webhook(self, event: dict, raw_body: bytes) -> VerifiedPaymentEvent | None:
        event_type = event.get("event_type", "")
        event_id = event.get("id", "")
        if not isinstance(event_type, str) or not isinstance(event_id, str) or not event_id:
            raise ProviderVerificationError("The PayPal event identity is invalid.")
        outcome_by_type = {
            "CHECKOUT.ORDER.APPROVED": "pending",
            "CHECKOUT.PAYMENT-APPROVAL.REVERSED": "cancelled",
            "PAYMENT.CAPTURE.PENDING": "pending",
            "PAYMENT.CAPTURE.COMPLETED": "paid",
            "PAYMENT.CAPTURE.DENIED": "failed",
        }
        outcome = outcome_by_type.get(event_type)
        if outcome is None:
            return None
        resource = event.get("resource")
        if not isinstance(resource, dict):
            raise ProviderVerificationError("The PayPal event resource is invalid.")
        supplementary_data = resource.get("supplementary_data") or {}
        if not isinstance(supplementary_data, dict):
            raise ProviderVerificationError("The PayPal event references are invalid.")
        related_ids = supplementary_data.get("related_ids") or {}
        if not isinstance(related_ids, dict):
            raise ProviderVerificationError("The PayPal event references are invalid.")
        related_order = related_ids.get("order_id")
        if related_order is not None and not isinstance(related_order, str):
            raise ProviderVerificationError("The PayPal order reference is invalid.")
        raw_transaction_reference = resource.get("id")
        if event_type.startswith("PAYMENT.CAPTURE.") and (
            not isinstance(raw_transaction_reference, str) or not raw_transaction_reference
        ):
            raise ProviderVerificationError("The PayPal capture reference is invalid.")
        transaction_reference = (
            raw_transaction_reference if event_type.startswith("PAYMENT.CAPTURE.") else ""
        )
        if (
            not transaction_reference
            and not related_order
            and not isinstance(raw_transaction_reference, str)
        ):
            raise ProviderVerificationError("The PayPal order reference is invalid.")
        provider_reference = related_order or raw_transaction_reference or ""
        attempt_query = PaymentAttempt.objects.filter(provider=PaymentAttempt.Provider.PAYPAL)
        attempt = None
        if provider_reference:
            attempt = (
                attempt_query.filter(provider_reference=provider_reference)
                .only("idempotency_key", "provider_reference")
                .first()
            )
        if attempt is None and transaction_reference:
            attempt = (
                attempt_query.filter(transaction_reference=transaction_reference)
                .only("idempotency_key", "provider_reference")
                .first()
            )
        if attempt is not None:
            provider_reference = attempt.provider_reference

        amount = resource.get("amount") or {}
        if not amount:
            units = resource.get("purchase_units") or []
            if not isinstance(units, list):
                raise ProviderVerificationError("The PayPal purchase units are invalid.")
            if units:
                first_unit = units[0]
                if not isinstance(first_unit, dict):
                    raise ProviderVerificationError("The PayPal purchase unit is invalid.")
                amount = first_unit.get("amount") or {}
        if not isinstance(amount, dict):
            raise ProviderVerificationError("The PayPal event amount is invalid.")
        try:
            currency = amount["currency_code"]
            if not isinstance(currency, str):
                raise ValueError("PayPal currency must be a code.")
            amount_value = Decimal(str(amount["value"]))
            if not amount_value.is_finite():
                raise ValueError("PayPal amount must be finite.")
            amount_minor = major_to_minor(amount_value, currency)
        except (
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
            ArithmeticError,
            LookupError,
        ) as error:
            raise ProviderVerificationError(
                "The PayPal event is missing a valid amount."
            ) from error
        if outcome == "paid" and not transaction_reference:
            raise ProviderVerificationError("The PayPal event is missing its capture reference.")

        return VerifiedPaymentEvent(
            provider=PaymentAttempt.Provider.PAYPAL,
            event_id=event_id,
            event_type=event_type,
            attempt_key=attempt.idempotency_key if attempt else None,
            provider_reference=provider_reference,
            payload_digest=hashlib.sha256(raw_body).hexdigest(),
            outcome=outcome,
            amount_minor=amount_minor,
            currency=str(currency).upper(),
            transaction_reference=transaction_reference,
        )
