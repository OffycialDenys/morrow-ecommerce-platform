import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import FreeGiveawayEntryForm
from .giveaways import (
    GiveawayError,
    giveaway_entry_window_is_open,
    record_giveaway_entries,
)
from .models import GiveawayCampaign, GiveawayEntry


def giveaway_list(request):
    campaigns = []
    if settings.GIVEAWAYS_ENABLED:
        now = timezone.now()
        candidates = GiveawayCampaign.objects.filter(
            status=GiveawayCampaign.Status.ACTIVE,
            entry_method=GiveawayCampaign.EntryMethod.FREE,
            starts_at__lte=now,
            ends_at__gt=now,
        ).order_by("ends_at", "pk")
        campaigns = [campaign for campaign in candidates if giveaway_entry_window_is_open(campaign)]
    return render(request, "promotions/giveaway_list.html", {"campaigns": campaigns})


def _public_campaign(slug: str) -> GiveawayCampaign:
    if not settings.GIVEAWAYS_ENABLED:
        raise Http404
    campaign = get_object_or_404(
        GiveawayCampaign,
        slug=slug,
        status=GiveawayCampaign.Status.ACTIVE,
        entry_method=GiveawayCampaign.EntryMethod.FREE,
    )
    if not giveaway_entry_window_is_open(campaign):
        raise Http404
    return campaign


def _giveaway_detail_context(request, campaign, form=None):
    customer_entries = None
    if request.user.is_authenticated:
        customer_entries = GiveawayEntry.objects.filter(
            campaign=campaign, customer=request.user
        ).count()
    return {
        "campaign": campaign,
        "entry_form": form
        or FreeGiveawayEntryForm(
            campaign=campaign,
            initial={"entry_nonce": uuid.uuid4().hex},
        ),
        "customer_entries": customer_entries,
        "remaining_entries": (
            max(0, campaign.max_entries_per_customer - customer_entries)
            if customer_entries is not None
            else None
        ),
    }


def giveaway_detail(request, slug: str):
    campaign = _public_campaign(slug)
    return render(
        request,
        "promotions/giveaway_detail.html",
        _giveaway_detail_context(request, campaign),
    )


@login_required
@require_POST
def enter_free_giveaway(request, slug: str):
    campaign = _public_campaign(slug)
    form = FreeGiveawayEntryForm(request.POST, campaign=campaign)
    if form.is_valid():
        try:
            tickets = record_giveaway_entries(
                campaign,
                request.user,
                source=GiveawayCampaign.EntryMethod.FREE,
                source_event_key=(
                    f"free:{request.user.pk}:{form.cleaned_data['entry_nonce']}"
                ),
                qualifier_count=1,
                region=form.cleaned_data["region"],
            )
        except GiveawayError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"Your entry is recorded ({len(tickets)} ticket(s)).")
            return redirect("promotions:giveaway-detail", slug=campaign.slug)
    return render(
        request,
        "promotions/giveaway_detail.html",
        _giveaway_detail_context(request, campaign, form),
        status=400,
    )
