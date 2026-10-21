from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

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


class ImmutableGiveawayEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Giveaway entries and audit events cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Giveaway entries and audit events cannot be changed.")

    def delete(self):
        raise ValidationError("Giveaway entries and audit events cannot be deleted.")


class GiveawayCampaign(models.Model):
    class EntryMethod(models.TextChoices):
        FREE = "free", "Free entry"
        PURCHASE = "purchase", "Qualifying purchase"
        POINTS = "points", "Points redemption"
        MANUAL = "manual", "Manually issued"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Rules approved"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        DRAWN = "drawn", "Winners selected"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    eligible_regions = models.JSONField(default=list)
    eligibility_criteria = models.TextField(blank=True)
    entry_method = models.CharField(max_length=12, choices=EntryMethod.choices, blank=True)
    entries_per_qualifier = models.PositiveIntegerField(null=True, blank=True)
    max_entries_per_customer = models.PositiveIntegerField(null=True, blank=True)
    prize_description = models.TextField(blank=True)
    prize_quantity = models.PositiveIntegerField(default=1)
    max_prizes_per_customer = models.PositiveIntegerField(default=1)
    official_rules_url = models.URLField(blank=True)
    official_disclosures = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    legal_review_reference = models.CharField(max_length=500, blank=True)
    rules_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_giveaway_rules",
        null=True,
        blank=True,
    )
    rules_approved_at = models.DateTimeField(null=True, blank=True)
    approved_rules_digest = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_giveaways",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="prom_giveaway_valid_window"
            ),
            models.CheckConstraint(
                condition=Q(prize_quantity__gt=0), name="prom_giveaway_prizes_positive"
            ),
            models.CheckConstraint(
                condition=Q(max_prizes_per_customer__gt=0),
                name="prom_giveaway_customer_prizes_positive",
            ),
        ]
        permissions = [
            ("manage_giveaways", "Can configure giveaways"),
            ("approve_giveaway_rules", "Can approve giveaway rules"),
            ("activate_giveaways", "Can activate and close giveaways"),
            ("draw_giveaway_winners", "Can perform a giveaway drawing"),
            ("approve_giveaway_draw", "Can independently approve a giveaway drawing"),
        ]

    def __str__(self) -> str:
        return self.name


class GiveawayEntry(models.Model):
    campaign = models.ForeignKey(
        GiveawayCampaign, on_delete=models.PROTECT, related_name="entries"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="giveaway_entries"
    )
    source = models.CharField(max_length=12, choices=GiveawayCampaign.EntryMethod.choices)
    source_event_key = models.CharField(max_length=160)
    ticket_number = models.PositiveIntegerField()
    event_key = models.CharField(max_length=255, unique=True)
    region = models.CharField(max_length=32, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_giveaway_entries",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableGiveawayEventQuerySet.as_manager()

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "source_event_key", "ticket_number"),
                name="prom_giveaway_event_ticket_unique",
            ),
            models.CheckConstraint(
                condition=Q(ticket_number__gt=0), name="prom_giveaway_ticket_positive"
            ),
        ]
        indexes = [
            models.Index(fields=("campaign", "customer"), name="prom_give_entry_customer_idx"),
        ]

    def __str__(self) -> str:
        return f"Entry {self.pk} for {self.campaign}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Giveaway entries cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Giveaway entries cannot be deleted.")


class GiveawayAuditEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = "created", "Campaign created"
        CONFIGURED = "configured", "Campaign configuration changed"
        RULES_APPROVED = "rules_approved", "Rules approved"
        DRAW_APPROVED = "draw_approved", "Drawing approved"
        ACTIVATED = "activated", "Campaign activated"
        CLOSED = "closed", "Campaign closed"
        CANCELLED = "cancelled", "Campaign cancelled"
        DRAWN = "drawn", "Winners selected"

    campaign = models.ForeignKey(
        GiveawayCampaign, on_delete=models.PROTECT, related_name="audit_events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="giveaway_audit_events"
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableGiveawayEventQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} for {self.campaign}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Giveaway audit events cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Giveaway audit events cannot be deleted.")


class GiveawayDraw(models.Model):
    campaign = models.OneToOneField(
        GiveawayCampaign, on_delete=models.PROTECT, related_name="draw"
    )
    entry_count = models.PositiveIntegerField()
    winner_count = models.PositiveIntegerField()
    participant_digest = models.CharField(max_length=64)
    seed_hex = models.CharField(max_length=64)
    seed_digest = models.CharField(max_length=64)
    algorithm = models.CharField(max_length=100)
    winner_entry_ids = models.JSONField()
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="performed_giveaway_draws"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approved_giveaway_draws"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableGiveawayEventQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(condition=~Q(operator=F("approver")), name="prom_draw_distinct_staff"),
        ]

    def __str__(self) -> str:
        return f"Drawing for {self.campaign}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Giveaway draw records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Giveaway draw records cannot be deleted.")


class GiveawayDrawApproval(models.Model):
    campaign = models.OneToOneField(
        GiveawayCampaign, on_delete=models.PROTECT, related_name="draw_approval"
    )
    entry_count = models.PositiveIntegerField()
    participant_digest = models.CharField(max_length=64)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_giveaway_draw_pools",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableGiveawayEventQuerySet.as_manager()

    class Meta:
        ordering = ("-approved_at", "-pk")

    def __str__(self) -> str:
        return f"Draw approval for {self.campaign}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Giveaway draw approvals cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Giveaway draw approvals cannot be deleted.")
