"""Cross-domain operational records that must remain independent of business data."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ImmutableAuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit events cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Audit events cannot be changed.")

    def delete(self):
        raise ValidationError("Audit events cannot be deleted.")


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        ACCOUNT_DATA_EXPORTED = "account_data_exported", "Customer account data exported"

    action = models.CharField(max_length=48, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_events_as_actor",
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_events_as_subject",
        null=True,
        blank=True,
    )
    request_id = models.UUIDField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableAuditEventQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("action", "-created_at"), name="core_audit_action_recent_idx"),
            models.Index(fields=("subject", "-created_at"), name="core_audit_subject_recent_idx"),
            models.Index(fields=("request_id",), name="core_audit_request_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} at {self.created_at:%Y-%m-%d %H:%M:%S}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit events cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events cannot be deleted.")
