import hashlib
import hmac
import json
import secrets
from datetime import datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.models import User

from .models import (
    GiveawayAuditEvent,
    GiveawayCampaign,
    GiveawayDraw,
    GiveawayDrawApproval,
    GiveawayEntry,
)

CAMPAIGN_RULE_FIELDS = (
    "name",
    "slug",
    "description",
    "starts_at",
    "ends_at",
    "eligible_regions",
    "eligibility_criteria",
    "entry_method",
    "entries_per_qualifier",
    "max_entries_per_customer",
    "prize_description",
    "prize_quantity",
    "max_prizes_per_customer",
    "official_rules_url",
    "official_disclosures",
    "legal_review_reference",
)


class GiveawayError(ValueError):
    """A giveaway operation does not satisfy its approval or eligibility rules."""


def _require_permission(actor, permission: str) -> None:
    if actor is None or not actor.has_perm(f"promotions.{permission}"):
        raise PermissionDenied("You do not have permission to perform this giveaway action.")


def _field_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rules_payload(campaign: GiveawayCampaign) -> dict:
    return {field: _field_value(getattr(campaign, field)) for field in CAMPAIGN_RULE_FIELDS}


def _rules_digest(campaign: GiveawayCampaign) -> str:
    payload = json.dumps(_rules_payload(campaign), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def giveaway_rules_are_current(campaign: GiveawayCampaign) -> bool:
    return bool(campaign.approved_rules_digest) and campaign.approved_rules_digest == _rules_digest(
        campaign
    )


def giveaway_entry_window_is_open(campaign: GiveawayCampaign) -> bool:
    now = timezone.now()
    return (
        getattr(settings, "GIVEAWAYS_ENABLED", False)
        and campaign.status == GiveawayCampaign.Status.ACTIVE
        and campaign.starts_at <= now < campaign.ends_at
        and giveaway_rules_are_current(campaign)
    )


def _participant_digest(entries: list[GiveawayEntry]) -> str:
    participant_data = "\n".join(f"{entry.pk}:{entry.customer_id}" for entry in entries)
    return hashlib.sha256(participant_data.encode("utf-8")).hexdigest()


def _record_event(campaign, actor, event_type: str, details: dict | None = None) -> None:
    GiveawayAuditEvent.objects.create(
        campaign=campaign,
        actor=actor,
        event_type=event_type,
        details=details or {},
    )


def save_giveaway_campaign(campaign: GiveawayCampaign, *, actor) -> GiveawayCampaign:
    """Create or edit draft campaign rules and record the configuration change."""
    _require_permission(actor, "manage_giveaways")

    with transaction.atomic():
        if campaign.pk:
            current = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
            if current.status != GiveawayCampaign.Status.DRAFT:
                raise GiveawayError("Return the campaign to draft before changing its rules.")
            changes = {
                field: {
                    "before": _field_value(getattr(current, field)),
                    "after": _field_value(getattr(campaign, field)),
                }
                for field in CAMPAIGN_RULE_FIELDS
                if getattr(current, field) != getattr(campaign, field)
            }
            campaign.created_by = current.created_by
            campaign.status = GiveawayCampaign.Status.DRAFT
            campaign.rules_approved_by = None
            campaign.rules_approved_at = None
            campaign.approved_rules_digest = ""
            event_type = GiveawayAuditEvent.EventType.CONFIGURED
        else:
            campaign.created_by = actor
            campaign.status = GiveawayCampaign.Status.DRAFT
            campaign.rules_approved_by = None
            campaign.rules_approved_at = None
            campaign.approved_rules_digest = ""
            changes = _rules_payload(campaign)
            event_type = GiveawayAuditEvent.EventType.CREATED

        campaign.full_clean()
        campaign.save()
        if changes:
            _record_event(campaign, actor, event_type, {"changes": changes})
        return campaign


def reset_giveaway_rules(campaign: GiveawayCampaign, *, actor) -> GiveawayCampaign:
    _require_permission(actor, "manage_giveaways")
    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status not in {GiveawayCampaign.Status.APPROVED}:
            raise GiveawayError("Only an approved campaign can return to draft.")
        old_digest = locked.approved_rules_digest
        locked.status = GiveawayCampaign.Status.DRAFT
        locked.rules_approved_by = None
        locked.rules_approved_at = None
        locked.approved_rules_digest = ""
        locked.save(
            update_fields=(
                "status",
                "rules_approved_by",
                "rules_approved_at",
                "approved_rules_digest",
                "updated_at",
            )
        )
        _record_event(
            locked,
            actor,
            GiveawayAuditEvent.EventType.CONFIGURED,
            {"action": "returned_to_draft", "previous_rules_digest": old_digest},
        )
        return locked


def _validate_approvable_rules(campaign: GiveawayCampaign) -> None:
    if not campaign.name.strip():
        raise GiveawayError("Give the giveaway a name before approval.")
    if campaign.ends_at <= campaign.starts_at:
        raise GiveawayError("The end time must be later than the start time.")
    regions = campaign.eligible_regions
    if not isinstance(regions, list) or not regions or any(not str(region).strip() for region in regions):
        raise GiveawayError("List every eligible region before approval.")
    if not campaign.eligibility_criteria.strip():
        raise GiveawayError("Document who may enter before approval.")
    if campaign.entry_method not in GiveawayCampaign.EntryMethod.values:
        raise GiveawayError("Choose the entry method stated in the official rules.")
    if not campaign.entries_per_qualifier or campaign.entries_per_qualifier < 1:
        raise GiveawayError("Set the number of entries issued for each qualifier.")
    if not campaign.max_entries_per_customer or campaign.max_entries_per_customer < 1:
        raise GiveawayError("Set a per-customer entry limit before approval.")
    if campaign.entries_per_qualifier > campaign.max_entries_per_customer:
        raise GiveawayError("The per-customer limit must allow at least one qualifying entry.")
    if not campaign.prize_description.strip() or campaign.prize_quantity < 1:
        raise GiveawayError("Describe the prize and its quantity before approval.")
    if not campaign.max_prizes_per_customer or campaign.max_prizes_per_customer < 1:
        raise GiveawayError("Set how many prizes one customer may receive.")
    if campaign.max_prizes_per_customer > campaign.prize_quantity:
        raise GiveawayError("A customer cannot receive more prizes than the campaign offers.")
    if not campaign.official_rules_url.strip() or not campaign.official_disclosures.strip():
        raise GiveawayError("Provide the official rules and required disclosures before approval.")
    if urlsplit(campaign.official_rules_url).scheme.lower() != "https":
        raise GiveawayError("The official rules must be published at an HTTPS address.")
    if not campaign.legal_review_reference.strip():
        raise GiveawayError("Record the qualified legal review reference before approval.")


def approve_giveaway_rules(campaign: GiveawayCampaign, *, reviewer) -> GiveawayCampaign:
    _require_permission(reviewer, "approve_giveaway_rules")
    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status != GiveawayCampaign.Status.DRAFT:
            raise GiveawayError("Only a draft campaign can be approved.")
        if locked.created_by_id == reviewer.pk:
            raise GiveawayError("A different staff member must approve the campaign rules.")
        _validate_approvable_rules(locked)
        digest = _rules_digest(locked)
        locked.status = GiveawayCampaign.Status.APPROVED
        locked.rules_approved_by = reviewer
        locked.rules_approved_at = timezone.now()
        locked.approved_rules_digest = digest
        locked.save(
            update_fields=(
                "status",
                "rules_approved_by",
                "rules_approved_at",
                "approved_rules_digest",
                "updated_at",
            )
        )
        _record_event(
            locked,
            reviewer,
            GiveawayAuditEvent.EventType.RULES_APPROVED,
            {"rules_digest": digest, "legal_review_reference": locked.legal_review_reference},
        )
        return locked


def activate_giveaway(campaign: GiveawayCampaign, *, actor) -> GiveawayCampaign:
    _require_permission(actor, "activate_giveaways")
    if not getattr(settings, "GIVEAWAYS_ENABLED", False):
        raise GiveawayError("Giveaways are disabled by the site launch setting.")
    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status != GiveawayCampaign.Status.APPROVED:
            raise GiveawayError("Approve the campaign rules before activation.")
        if locked.rules_approved_by_id == actor.pk:
            raise GiveawayError("The rules approver cannot activate the campaign.")
        if locked.approved_rules_digest != _rules_digest(locked):
            raise GiveawayError("The campaign rules changed after approval; approve them again.")
        now = timezone.now()
        if not locked.starts_at <= now < locked.ends_at:
            raise GiveawayError("The campaign can only be active during its approved entry window.")
        locked.status = GiveawayCampaign.Status.ACTIVE
        locked.save(update_fields=("status", "updated_at"))
        _record_event(
            locked,
            actor,
            GiveawayAuditEvent.EventType.ACTIVATED,
            {"rules_digest": locked.approved_rules_digest},
        )
        return locked


def close_giveaway(campaign: GiveawayCampaign, *, actor) -> GiveawayCampaign:
    _require_permission(actor, "activate_giveaways")
    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status != GiveawayCampaign.Status.ACTIVE:
            raise GiveawayError("Only an active campaign can be closed.")
        if timezone.now() < locked.ends_at:
            raise GiveawayError("The campaign cannot close before its approved end time.")
        locked.status = GiveawayCampaign.Status.CLOSED
        locked.save(update_fields=("status", "updated_at"))
        _record_event(locked, actor, GiveawayAuditEvent.EventType.CLOSED)
        return locked


def cancel_giveaway(campaign: GiveawayCampaign, *, actor, reason: str) -> GiveawayCampaign:
    _require_permission(actor, "activate_giveaways")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 240:
        raise GiveawayError("A short cancellation reason is required.")
    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status not in {GiveawayCampaign.Status.APPROVED, GiveawayCampaign.Status.ACTIVE}:
            raise GiveawayError("Only an approved or active campaign can be cancelled.")
        locked.status = GiveawayCampaign.Status.CANCELLED
        locked.save(update_fields=("status", "updated_at"))
        _record_event(
            locked, actor, GiveawayAuditEvent.EventType.CANCELLED, {"reason": reason.strip()}
        )
        return locked


def record_giveaway_entries(
    campaign: GiveawayCampaign,
    customer: User,
    *,
    source: str,
    source_event_key: str,
    qualifier_count: int,
    region: str,
    actor=None,
) -> list[GiveawayEntry]:
    """Issue idempotent ticket rows from a trusted, configured qualification event."""
    if not getattr(settings, "GIVEAWAYS_ENABLED", False):
        raise GiveawayError("Giveaways are disabled by the site launch setting.")
    if not isinstance(source_event_key, str) or not source_event_key.strip():
        raise GiveawayError("A stable qualification event key is required.")
    if type(qualifier_count) is not int or qualifier_count < 1:  # noqa: E721
        raise GiveawayError("The number of qualifying events must be a positive whole number.")
    if len(source_event_key.strip()) > 160:
        raise GiveawayError("The qualification event key is too long.")
    if not isinstance(region, str) or not region.strip() or len(region.strip()) > 32:
        raise GiveawayError("Provide the customer's eligible region.")
    source_event_key = source_event_key.strip()
    region = region.strip().upper()
    if source == GiveawayCampaign.EntryMethod.MANUAL:
        _require_permission(actor, "manage_giveaways")

    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        now = timezone.now()
        if locked.status != GiveawayCampaign.Status.ACTIVE or not locked.starts_at <= now < locked.ends_at:
            raise GiveawayError("Entries can only be issued during an active campaign.")
        if locked.approved_rules_digest != _rules_digest(locked):
            raise GiveawayError("The approved campaign rules have changed.")
        if source != locked.entry_method:
            raise GiveawayError("This entry source is not allowed by the campaign rules.")
        eligible_regions = {str(item).strip().upper() for item in locked.eligible_regions}
        if "ALL" not in eligible_regions and region not in eligible_regions:
            raise GiveawayError("This customer is outside the eligible regions.")
        count = qualifier_count * locked.entries_per_qualifier
        existing = list(
            GiveawayEntry.objects.filter(
                campaign=locked, source_event_key=source_event_key
            ).order_by("ticket_number")
        )
        if existing:
            if (
                len(existing) == count
                and all(
                    item.customer_id == customer.pk
                    and item.source == source
                    and item.region == region
                    for item in existing
                )
            ):
                return existing
            raise GiveawayError("That qualification event key already has different entries.")

        current_count = GiveawayEntry.objects.filter(
            campaign=locked, customer=customer
        ).count()
        if count > locked.max_entries_per_customer - current_count:
            raise GiveawayError("This customer has reached the campaign entry limit.")

        if count > 10000:
            raise GiveawayError("This qualification would exceed the safe entry batch size.")

        return [
            GiveawayEntry.objects.create(
                campaign=locked,
                customer=customer,
                source=source,
                source_event_key=source_event_key,
                ticket_number=ticket_number,
                event_key=f"giveaway:{locked.pk}:{source_event_key}:{ticket_number}",
                region=region,
                actor=actor,
            )
            for ticket_number in range(1, count + 1)
        ]


def approve_giveaway_draw(campaign: GiveawayCampaign, *, approver) -> GiveawayDrawApproval:
    _require_permission(approver, "approve_giveaway_draw")
    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status != GiveawayCampaign.Status.CLOSED:
            raise GiveawayError("Close the campaign before approving a drawing.")
        if locked.approved_rules_digest != _rules_digest(locked):
            raise GiveawayError("The approved campaign rules have changed.")
        if locked.rules_approved_by_id == approver.pk:
            raise GiveawayError("A different staff member must approve the drawing pool.")
        entries = list(
            GiveawayEntry.objects.filter(campaign=locked)
            .order_by("pk")
            .only("pk", "customer_id")
        )
        if not entries:
            raise GiveawayError("A campaign needs eligible entries before the drawing can be approved.")
        digest = _participant_digest(entries)
        eligible_customers = {entry.customer_id for entry in entries}
        if len(eligible_customers) * locked.max_prizes_per_customer < locked.prize_quantity:
            raise GiveawayError("There are not enough eligible customers to award every prize.")
        existing = GiveawayDrawApproval.objects.filter(campaign=locked).first()
        if existing is not None:
            if existing.approver_id == approver.pk and existing.participant_digest == digest:
                return existing
            raise GiveawayError("A different drawing pool has already been approved.")
        approval = GiveawayDrawApproval.objects.create(
            campaign=locked,
            entry_count=len(entries),
            participant_digest=digest,
            approver=approver,
        )
        _record_event(
            locked,
            approver,
            GiveawayAuditEvent.EventType.DRAW_APPROVED,
            {"entry_count": len(entries), "participant_digest": digest},
        )
        return approval


def draw_giveaway_winners(campaign: GiveawayCampaign, *, operator) -> GiveawayDraw:
    """Record one auditable draw with a cryptographically seeded ticket ordering."""
    _require_permission(operator, "draw_giveaway_winners")
    if not getattr(settings, "GIVEAWAYS_ENABLED", False):
        raise GiveawayError("Giveaways are disabled by the site launch setting.")

    with transaction.atomic():
        locked = GiveawayCampaign.objects.select_for_update().get(pk=campaign.pk)
        if locked.status != GiveawayCampaign.Status.CLOSED:
            raise GiveawayError("Close the campaign before selecting winners.")
        if locked.approved_rules_digest != _rules_digest(locked):
            raise GiveawayError("The approved campaign rules have changed.")
        if GiveawayDraw.objects.filter(campaign=locked).exists():
            raise GiveawayError("This campaign already has a drawing record.")
        approval = GiveawayDrawApproval.objects.select_for_update().filter(
            campaign=locked
        ).first()
        if approval is None:
            raise GiveawayError("An independent staff member must approve the drawing pool first.")
        if approval.approver_id == operator.pk or locked.rules_approved_by_id == operator.pk:
            raise GiveawayError("The drawing operator must be independent of the approvers.")

        entries = list(
            GiveawayEntry.objects.filter(campaign=locked)
            .select_related("customer")
            .order_by("pk")
        )
        participant_digest = _participant_digest(entries)
        if (
            len(entries) != approval.entry_count
            or participant_digest != approval.participant_digest
        ):
            raise GiveawayError("The eligible entry pool changed after drawing approval.")
        eligible_customers = {entry.customer_id for entry in entries}
        if len(eligible_customers) * locked.max_prizes_per_customer < locked.prize_quantity:
            raise GiveawayError("There are not enough eligible customers to award every prize.")

        seed = secrets.token_bytes(32)
        seed_hex = seed.hex()
        ordered_entries = sorted(
            entries,
            key=lambda entry: hmac.new(
                seed, f"{locked.pk}:{entry.pk}".encode("ascii"), hashlib.sha256
            ).digest(),
        )
        wins_by_customer: dict[int, int] = {}
        winner_entry_ids = []
        for entry in ordered_entries:
            wins = wins_by_customer.get(entry.customer_id, 0)
            if wins >= locked.max_prizes_per_customer:
                continue
            winner_entry_ids.append(entry.pk)
            wins_by_customer[entry.customer_id] = wins + 1
            if len(winner_entry_ids) == locked.prize_quantity:
                break

        if len(winner_entry_ids) != locked.prize_quantity:
            raise GiveawayError("The eligible pool cannot satisfy the approved winner limits.")

        draw = GiveawayDraw.objects.create(
            campaign=locked,
            entry_count=len(entries),
            winner_count=len(winner_entry_ids),
            participant_digest=participant_digest,
            seed_hex=seed_hex,
            seed_digest=hashlib.sha256(seed).hexdigest(),
            algorithm="HMAC-SHA256 rank by campaign and ticket ID; enforce approved customer prize limit",
            winner_entry_ids=winner_entry_ids,
            operator=operator,
            approver=approval.approver,
        )
        locked.status = GiveawayCampaign.Status.DRAWN
        locked.save(update_fields=("status", "updated_at"))
        _record_event(
            locked,
            operator,
            GiveawayAuditEvent.EventType.DRAWN,
            {
                "entry_count": len(entries),
                "winner_entry_ids": winner_entry_ids,
                "participant_digest": participant_digest,
                "seed_digest": draw.seed_digest,
                "approver_id": approval.approver_id,
            },
        )
        return draw
