from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from commerce.models import Order

from .models import PointAdjustmentAuditEvent, PointAdjustmentRequest, PointEntry

MAX_POINTS = 2**63 - 1


class PointsError(ValueError):
    """A points-ledger operation does not satisfy its business rules."""


def _validate_entry(event_key: str, reason: str) -> tuple[str, str]:
    if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 200:
        raise PointsError("A unique points event key is required.")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 240:
        raise PointsError("Provide a short reason for this points entry.")
    return event_key.strip(), reason.strip()


def get_points_balance(customer) -> int:
    return (
        PointEntry.objects.filter(customer=customer).aggregate(balance=Sum("points"))["balance"]
        or 0
    )


def _existing_entry(event_key: str, *, customer, points: int, event_type: str, reason: str):
    entry = PointEntry.objects.filter(event_key=event_key).first()
    if entry is None:
        return None
    if (
        entry.customer_id != customer.pk
        or entry.points != points
        or entry.event_type != event_type
        or entry.reason != reason
    ):
        raise PointsError("That points event key was already used for a different entry.")
    return entry


def credit_paid_order_points(order: Order) -> int:
    """Post configured per-unit points once a paid order is authoritative."""
    if not getattr(settings, "POINTS_PROGRAM_ENABLED", False):
        return 0

    earned = 0
    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if locked_order.payment_status != Order.PaymentStatus.PAID:
            raise PointsError("Points can only be credited for a paid order.")

        for line in locked_order.lines.order_by("pk"):
            points = line.points_per_unit * line.quantity
            if points <= 0:
                continue
            if points > MAX_POINTS:
                raise PointsError("The configured points amount exceeds the supported range.")
            event_key = f"purchase:{locked_order.order_number.hex}:{line.pk}"
            reason = "Points earned on a qualifying paid order line."
            existing = _existing_entry(
                event_key,
                customer=locked_order.user,
                points=points,
                event_type=PointEntry.EventType.PURCHASE,
                reason=reason,
            )
            if existing is not None:
                continue
            PointEntry.objects.create(
                customer=locked_order.user,
                order=locked_order,
                order_line=line,
                event_type=PointEntry.EventType.PURCHASE,
                points=points,
                event_key=event_key,
                reason=reason,
            )
            earned += points
    return earned


def reverse_point_entry(
    entry: PointEntry,
    *,
    points: int,
    event_key: str,
    reason: str,
    actor,
) -> PointEntry:
    """Add a partial or full compensating entry without editing the original."""
    event_key, reason = _validate_entry(event_key, reason)
    if type(points) is not int or not 1 <= points <= MAX_POINTS:
        raise PointsError("Choose a positive number of points to reverse.")
    if actor is None or not actor.has_perm("promotions.adjust_points"):
        raise PermissionDenied("You do not have permission to reverse points.")

    with transaction.atomic():
        original = PointEntry.objects.select_for_update().get(pk=entry.pk)
        if original.event_type == PointEntry.EventType.REVERSAL:
            raise PointsError("A reversal cannot itself be reversed.")
        existing = _existing_entry(
            event_key,
            customer=original.customer,
            points=-points if original.points > 0 else points,
            event_type=PointEntry.EventType.REVERSAL,
            reason=reason,
        )
        if existing is not None:
            if existing.reversal_of_id != original.pk:
                raise PointsError("That points event key belongs to a different reversal.")
            return existing

        prior_reversals = original.reversals.aggregate(total=Sum("points"))["total"] or 0
        reversed_points = abs(prior_reversals)
        if points > abs(original.points) - reversed_points:
            raise PointsError("The reversal is greater than the unreversed points balance.")

        signed_points = -points if original.points > 0 else points
        return PointEntry.objects.create(
            customer=original.customer,
            order=original.order,
            order_line=original.order_line,
            event_type=PointEntry.EventType.REVERSAL,
            points=signed_points,
            event_key=event_key,
            reversal_of=original,
            actor=actor,
            reason=reason,
        )


def request_point_adjustment(
    customer,
    *,
    points: int,
    event_key: str,
    requester,
    reason: str,
) -> PointAdjustmentRequest:
    """Record an idempotent request that another staff member must resolve."""
    event_key, reason = _validate_entry(event_key, reason)
    if type(points) is not int or points == 0 or abs(points) > MAX_POINTS:
        raise PointsError("A manual adjustment must be a non-zero whole number of points.")
    if requester is None or not requester.has_perm("promotions.request_point_adjustment"):
        raise PermissionDenied("You do not have permission to request a points adjustment.")
    if requester.pk == customer.pk:
        raise PointsError("Staff cannot request an adjustment to their own points balance.")

    with transaction.atomic():
        customer = type(customer).objects.select_for_update().get(pk=customer.pk)
        existing = PointAdjustmentRequest.objects.filter(event_key=event_key).first()
        if existing is not None:
            if (
                existing.customer_id != customer.pk
                or existing.points != points
                or existing.reason != reason
                or existing.requested_by_id != requester.pk
            ):
                raise PointsError("That adjustment key was already used for a different request.")
            return existing
        if PointEntry.objects.filter(event_key=event_key).exists():
            raise PointsError("That adjustment key is already used by the points ledger.")
        try:
            with transaction.atomic():
                adjustment = PointAdjustmentRequest.objects.create(
                    customer=customer,
                    points=points,
                    event_key=event_key,
                    reason=reason,
                    requested_by=requester,
                )
        except IntegrityError:
            existing = PointAdjustmentRequest.objects.filter(event_key=event_key).first()
            if (
                existing is not None
                and existing.customer_id == customer.pk
                and existing.points == points
                and existing.reason == reason
                and existing.requested_by_id == requester.pk
            ):
                return existing
            raise PointsError("That adjustment key was already used for a different request.")
        PointAdjustmentAuditEvent.objects.create(
            adjustment=adjustment,
            actor=requester,
            event_type=PointAdjustmentAuditEvent.EventType.REQUESTED,
            details={"customer_id": customer.pk, "points": points, "event_key": event_key},
        )
        return adjustment


def approve_point_adjustment(
    adjustment: PointAdjustmentRequest, *, approver
) -> PointEntry:
    """Post the ledger entry after an independent staff member approves it."""
    if approver is None or not approver.has_perm("promotions.approve_point_adjustment"):
        raise PermissionDenied("You do not have permission to approve points adjustments.")

    with transaction.atomic():
        locked = PointAdjustmentRequest.objects.select_for_update().get(pk=adjustment.pk)
        if locked.status == PointAdjustmentRequest.Status.APPROVED:
            return locked.ledger_entry
        if locked.status != PointAdjustmentRequest.Status.PENDING:
            raise PointsError("This adjustment request has already been rejected.")
        if locked.requested_by_id == approver.pk:
            raise PointsError("A different staff member must approve the adjustment.")

        customer = type(locked.customer).objects.select_for_update().get(
            pk=locked.customer_id
        )
        balance = get_points_balance(customer)
        if not -MAX_POINTS <= balance + locked.points <= MAX_POINTS:
            raise PointsError("This adjustment would exceed the supported points balance.")

        if PointEntry.objects.filter(event_key=locked.event_key).exists():
            raise PointsError("That points event key is already used by the ledger.")
        try:
            with transaction.atomic():
                entry = PointEntry.objects.create(
                    customer=customer,
                    event_type=PointEntry.EventType.ADJUSTMENT,
                    points=locked.points,
                    event_key=locked.event_key,
                    actor=approver,
                    reason=locked.reason,
                )
        except IntegrityError as error:
            raise PointsError("That points event key is already used by the ledger.") from error

        now = timezone.now()
        locked.status = PointAdjustmentRequest.Status.APPROVED
        locked.resolved_by = approver
        locked.ledger_entry = entry
        locked.resolution_reason = "Approved."
        locked.resolved_at = now
        locked.save(
            update_fields=(
                "status",
                "resolved_by",
                "ledger_entry",
                "resolution_reason",
                "resolved_at",
            )
        )
        PointAdjustmentAuditEvent.objects.create(
            adjustment=locked,
            actor=approver,
            event_type=PointAdjustmentAuditEvent.EventType.APPROVED,
            details={"ledger_entry_id": entry.pk, "points": entry.points},
        )
        return entry


def reject_point_adjustment(
    adjustment: PointAdjustmentRequest, *, reviewer, reason: str
) -> PointAdjustmentRequest:
    if reviewer is None or not reviewer.has_perm("promotions.approve_point_adjustment"):
        raise PermissionDenied("You do not have permission to reject points adjustments.")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 240:
        raise PointsError("Provide a short reason for rejecting the adjustment.")

    with transaction.atomic():
        locked = PointAdjustmentRequest.objects.select_for_update().get(pk=adjustment.pk)
        if locked.status == PointAdjustmentRequest.Status.REJECTED:
            if locked.resolved_by_id == reviewer.pk and locked.resolution_reason == reason.strip():
                return locked
            raise PointsError("This adjustment request has already been resolved.")
        if locked.status != PointAdjustmentRequest.Status.PENDING:
            raise PointsError("An approved adjustment cannot be rejected.")
        if locked.requested_by_id == reviewer.pk:
            raise PointsError("A different staff member must reject the adjustment.")

        now = timezone.now()
        locked.status = PointAdjustmentRequest.Status.REJECTED
        locked.resolved_by = reviewer
        locked.resolution_reason = reason.strip()
        locked.resolved_at = now
        locked.save(
            update_fields=("status", "resolved_by", "resolution_reason", "resolved_at")
        )
        PointAdjustmentAuditEvent.objects.create(
            adjustment=locked,
            actor=reviewer,
            event_type=PointAdjustmentAuditEvent.EventType.REJECTED,
            details={"reason": locked.resolution_reason},
        )
        return locked
