"""Allowlisted, immutable audit records for sensitive cross-domain actions."""

from uuid import UUID

from .models import AuditEvent


def record_audit_event(*, action: str, request_id: UUID, actor=None, subject=None) -> AuditEvent:
    if action not in AuditEvent.Action.values:
        raise ValueError("Audit action is not allowlisted.")
    return AuditEvent.objects.create(
        action=action,
        request_id=request_id,
        actor=actor,
        subject=subject,
    )
