from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from commerce.models import Order, OrderLine


class ImmutableLedgerQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Points ledger entries cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Points ledger entries cannot be changed.")

    def delete(self):
        raise ValidationError("Points ledger entries cannot be deleted.")


class PointEntry(models.Model):
    class EventType(models.TextChoices):
        PURCHASE = "purchase", "Qualifying purchase"
        REVERSAL = "reversal", "Reversal"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        BONUS = "bonus", "Promotional bonus"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="point_entries"
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="point_entries",
        null=True,
        blank=True,
    )
    order_line = models.ForeignKey(
        OrderLine,
        on_delete=models.PROTECT,
        related_name="point_entries",
        null=True,
        blank=True,
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    points = models.BigIntegerField()
    event_key = models.CharField(max_length=200, unique=True)
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="reversals",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_point_entries",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableLedgerQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(condition=~Q(points=0), name="prom_points_nonzero"),
        ]
        indexes = [
            models.Index(fields=("customer", "-created_at"), name="prom_entry_customer_recent_idx"),
            models.Index(fields=("order", "event_type"), name="prom_entry_order_type_idx"),
        ]
        permissions = [
            ("adjust_points", "Can create manual points adjustments"),
        ]

    def __str__(self) -> str:
        return f"{self.points:+} points for {self.customer}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Points ledger entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Points ledger entries cannot be deleted.")
