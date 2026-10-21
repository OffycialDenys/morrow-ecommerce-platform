"""Payment state services. Provider adapters must verify webhooks before calling these."""

import re
from dataclasses import dataclass
from uuid import UUID

from django.db import IntegrityError, transaction

from .models import Order, PaymentAttempt, PaymentEvent


class PaymentError(ValueError):
    """A payment operation does not match the current order state."""


@dataclass(frozen=True)
class VerifiedPaymentEvent:
    """Normalized provider data returned only after provider-specific verification."""

    provider: str
    event_id: str
    event_type: str
    attempt_key: UUID | None
    provider_reference: str
    payload_digest: str
    outcome: str
    amount_minor: int
    currency: str
    transaction_reference: str = ""


def create_payment_attempt(order: Order, *, provider: str, idempotency_key: UUID) -> PaymentAttempt:
    if provider not in PaymentAttempt.Provider.values:
        raise PaymentError("Choose a supported payment provider.")
    if not isinstance(idempotency_key, UUID):
        raise PaymentError("A valid payment idempotency key is required.")

    with transaction.atomic():
        existing = (
            PaymentAttempt.objects.select_for_update()
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing is not None:
            if existing.order_id != order.pk or existing.provider != provider:
                raise PaymentError("That payment request key was already used.")
            return existing

        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if locked_order.payment_status in {
            Order.PaymentStatus.PAID,
            Order.PaymentStatus.PARTIALLY_REFUNDED,
            Order.PaymentStatus.REFUNDED,
            Order.PaymentStatus.CANCELLED,
            Order.PaymentStatus.NEEDS_REVIEW,
        }:
            raise PaymentError("This order cannot accept another payment attempt.")

        active = (
            PaymentAttempt.objects.select_for_update()
            .filter(
                order=locked_order,
                status__in=(PaymentAttempt.Status.CREATED, PaymentAttempt.Status.AWAITING_CUSTOMER),
            )
            .first()
        )
        if active is not None:
            raise PaymentError("A payment attempt is already open for this order.")

        try:
            with transaction.atomic():
                attempt = PaymentAttempt.objects.create(
                    order=locked_order,
                    provider=provider,
                    idempotency_key=idempotency_key,
                    amount_minor=locked_order.total_minor,
                    currency=locked_order.currency,
                )
                if locked_order.payment_status == Order.PaymentStatus.FAILED:
                    locked_order.payment_status = Order.PaymentStatus.PENDING
                    locked_order.save(update_fields=("payment_status", "updated_at"))
        except IntegrityError:
            existing = PaymentAttempt.objects.filter(idempotency_key=idempotency_key).first()
            if existing and existing.order_id == locked_order.pk and existing.provider == provider:
                return existing
            raise PaymentError("A payment attempt is already open for this order.")
        return attempt


def set_payment_attempt_reference(
    attempt: PaymentAttempt, provider_reference: str
) -> PaymentAttempt:
    reference = provider_reference.strip()
    if not reference or len(reference) > 200:
        raise PaymentError("The payment provider returned an invalid reference.")

    with transaction.atomic():
        locked = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status not in {
            PaymentAttempt.Status.CREATED,
            PaymentAttempt.Status.AWAITING_CUSTOMER,
        }:
            raise PaymentError("This payment attempt is no longer open.")
        if locked.provider_reference and locked.provider_reference != reference:
            raise PaymentError("A different provider reference is already attached.")
        locked.provider_reference = reference
        locked.status = PaymentAttempt.Status.AWAITING_CUSTOMER
        locked.save(update_fields=("provider_reference", "status", "updated_at"))
        return locked


def _event_status(event: VerifiedPaymentEvent, attempt: PaymentAttempt, order: Order) -> str:
    if (
        event.amount_minor != attempt.amount_minor
        or event.currency.upper() != attempt.currency
        or attempt.amount_minor != order.total_minor
        or attempt.currency != order.currency
    ):
        return PaymentEvent.Status.REJECTED
    if attempt.provider_reference and event.provider_reference:
        if attempt.provider_reference != event.provider_reference:
            return PaymentEvent.Status.REJECTED
    if attempt.transaction_reference and event.transaction_reference:
        if attempt.transaction_reference != event.transaction_reference:
            return PaymentEvent.Status.REJECTED
    if (
        event.transaction_reference
        and PaymentAttempt.objects.filter(
            provider=event.provider,
            transaction_reference=event.transaction_reference,
        )
        .exclude(pk=attempt.pk)
        .exists()
    ):
        return PaymentEvent.Status.REJECTED
    if event.outcome == "pending":
        return (
            PaymentEvent.Status.PROCESSED
            if attempt.status
            in {PaymentAttempt.Status.CREATED, PaymentAttempt.Status.AWAITING_CUSTOMER}
            and order.payment_status == Order.PaymentStatus.PENDING
            else PaymentEvent.Status.IGNORED
        )
    if event.outcome == "failed":
        if attempt.status in {
            PaymentAttempt.Status.SUCCEEDED,
            PaymentAttempt.Status.CANCELLED,
            PaymentAttempt.Status.REFUNDED,
        }:
            return PaymentEvent.Status.IGNORED
        if order.payment_status == Order.PaymentStatus.PAID:
            return PaymentEvent.Status.IGNORED
        if order.payment_status in {
            Order.PaymentStatus.REFUNDED,
            Order.PaymentStatus.PARTIALLY_REFUNDED,
            Order.PaymentStatus.CANCELLED,
        }:
            return PaymentEvent.Status.IGNORED
        return PaymentEvent.Status.PROCESSED
    if event.outcome == "cancelled":
        return (
            PaymentEvent.Status.PROCESSED
            if attempt.status
            in {PaymentAttempt.Status.CREATED, PaymentAttempt.Status.AWAITING_CUSTOMER}
            and order.payment_status == Order.PaymentStatus.PENDING
            else PaymentEvent.Status.IGNORED
        )
    if event.outcome == "paid":
        if order.payment_status in {
            Order.PaymentStatus.REFUNDED,
            Order.PaymentStatus.PARTIALLY_REFUNDED,
            Order.PaymentStatus.CANCELLED,
        }:
            return PaymentEvent.Status.REJECTED
        other_success = PaymentAttempt.objects.filter(
            order=order, status=PaymentAttempt.Status.SUCCEEDED
        ).exclude(pk=attempt.pk)
        return (
            PaymentEvent.Status.REJECTED
            if other_success.exists()
            else PaymentEvent.Status.PROCESSED
        )
    return PaymentEvent.Status.REJECTED


def _apply_event_state(event: VerifiedPaymentEvent, attempt: PaymentAttempt, order: Order) -> None:
    if event.provider_reference and not attempt.provider_reference:
        attempt.provider_reference = event.provider_reference
    if event.transaction_reference and not attempt.transaction_reference:
        attempt.transaction_reference = event.transaction_reference

    if event.outcome == "pending":
        attempt.status = PaymentAttempt.Status.AWAITING_CUSTOMER
        attempt.save(
            update_fields=("provider_reference", "transaction_reference", "status", "updated_at")
        )
    elif event.outcome == "paid":
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.save(
            update_fields=("provider_reference", "transaction_reference", "status", "updated_at")
        )
        if order.payment_status in {
            Order.PaymentStatus.PENDING,
            Order.PaymentStatus.FAILED,
        }:
            order.payment_status = Order.PaymentStatus.PAID
            order.save(update_fields=("payment_status", "updated_at"))
            from django.conf import settings

            if settings.POINTS_PROGRAM_ENABLED:
                from promotions.points import credit_paid_order_points

                credit_paid_order_points(order)
    elif event.outcome == "failed":
        attempt.status = PaymentAttempt.Status.FAILED
        attempt.save(
            update_fields=("provider_reference", "transaction_reference", "status", "updated_at")
        )
        if order.payment_status == Order.PaymentStatus.PENDING:
            order.payment_status = Order.PaymentStatus.FAILED
            order.save(update_fields=("payment_status", "updated_at"))
    elif event.outcome == "cancelled":
        attempt.status = PaymentAttempt.Status.CANCELLED
        attempt.save(
            update_fields=("provider_reference", "transaction_reference", "status", "updated_at")
        )


def process_verified_payment_event(event: VerifiedPaymentEvent) -> str:
    """Apply a normalized event only after its provider signature has been verified."""
    if event.provider not in PaymentAttempt.Provider.values:
        raise PaymentError("Unsupported payment provider.")
    if (
        not isinstance(event.event_id, str)
        or not event.event_id
        or len(event.event_id) > 255
        or not isinstance(event.event_type, str)
        or not event.event_type
        or len(event.event_type) > 120
    ):
        raise PaymentError("The payment event is missing its provider identity.")
    if event.attempt_key is not None and not isinstance(event.attempt_key, UUID):
        raise PaymentError("The payment event has an invalid internal attempt reference.")
    if (
        not isinstance(event.provider_reference, str)
        or not isinstance(event.transaction_reference, str)
        or len(event.provider_reference) > 200
        or len(event.transaction_reference) > 200
    ):
        raise PaymentError("The payment event has an invalid provider reference.")
    if event.attempt_key is None and not event.provider_reference:
        raise PaymentError("The payment event has no provider reference.")
    if type(event.amount_minor) is not int or not 0 <= event.amount_minor <= 2**63 - 1:  # noqa: E721
        raise PaymentError("The payment event has an invalid amount.")
    if not isinstance(event.payload_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", event.payload_digest
    ):
        raise PaymentError("The payment event has an invalid digest.")
    if not isinstance(event.currency, str) or not re.fullmatch(r"[A-Z]{3}", event.currency):
        raise PaymentError("The payment event has an invalid currency.")
    if event.outcome not in {"pending", "paid", "failed", "cancelled"}:
        raise PaymentError("The payment event has an unsupported outcome.")

    with transaction.atomic():
        attempt_query = PaymentAttempt.objects.select_for_update().filter(provider=event.provider)
        if event.attempt_key is not None:
            attempt_query = attempt_query.filter(idempotency_key=event.attempt_key)
        else:
            attempt_query = attempt_query.filter(provider_reference=event.provider_reference)
        attempt = attempt_query.first()
        previous = PaymentEvent.objects.filter(
            provider=event.provider, provider_event_id=event.event_id
        ).first()
        if previous is not None:
            return "duplicate" if previous.payload_digest == event.payload_digest else "rejected"

        if attempt is None:
            status = PaymentEvent.Status.REJECTED
        else:
            order = Order.objects.select_for_update().get(pk=attempt.order_id)
            status = _event_status(event, attempt, order)

        try:
            with transaction.atomic():
                receipt = PaymentEvent.objects.create(
                    provider=event.provider,
                    provider_event_id=event.event_id,
                    event_type=event.event_type,
                    provider_reference=event.provider_reference[:200],
                    transaction_reference=event.transaction_reference[:200],
                    attempt=attempt,
                    payload_digest=event.payload_digest,
                    reported_amount_minor=event.amount_minor,
                    reported_currency=event.currency[:16],
                    status=status,
                )
        except IntegrityError:
            previous = PaymentEvent.objects.filter(
                provider=event.provider, provider_event_id=event.event_id
            ).first()
            if previous is not None:
                return (
                    "duplicate" if previous.payload_digest == event.payload_digest else "rejected"
                )
            raise

        if attempt is None:
            receipt.processed_at = receipt.received_at
            receipt.save(update_fields=("processed_at",))
            return "rejected"
        if status == PaymentEvent.Status.REJECTED:
            if event.outcome == "paid":
                attempt.status = PaymentAttempt.Status.SUCCEEDED
                attempt.save(update_fields=("status", "updated_at"))
                order.payment_status = Order.PaymentStatus.NEEDS_REVIEW
                order.save(update_fields=("payment_status", "updated_at"))
            receipt.processed_at = receipt.received_at
            receipt.save(update_fields=("processed_at",))
            return "rejected"
        if status == PaymentEvent.Status.IGNORED:
            receipt.processed_at = receipt.received_at
            receipt.save(update_fields=("processed_at",))
            return "ignored"

        try:
            with transaction.atomic():
                _apply_event_state(event, attempt, order)
        except IntegrityError:
            receipt.status = PaymentEvent.Status.REJECTED
            if event.outcome == "paid":
                attempt.status = PaymentAttempt.Status.SUCCEEDED
                attempt.save(update_fields=("status", "updated_at"))
                order.payment_status = Order.PaymentStatus.NEEDS_REVIEW
                order.save(update_fields=("payment_status", "updated_at"))
        receipt.processed_at = receipt.received_at
        receipt.save(update_fields=("status", "processed_at"))
        return "rejected" if receipt.status == PaymentEvent.Status.REJECTED else "processed"
