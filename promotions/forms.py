import uuid

from django import forms

from .models import GiveawayCampaign


class FreeGiveawayEntryForm(forms.Form):
    entry_nonce = forms.CharField(
        min_length=32,
        max_length=32,
        widget=forms.HiddenInput,
    )
    region = forms.CharField(max_length=32, strip=True, label="Your eligible region")

    def __init__(self, *args, campaign: GiveawayCampaign, **kwargs):
        super().__init__(*args, **kwargs)
        regions = [str(region).strip().upper() for region in campaign.eligible_regions]
        if "ALL" not in regions:
            self.fields["region"] = forms.ChoiceField(
                label="Your eligible region",
                choices=[("", "Choose your region"), *((region, region) for region in regions)],
            )

    def clean_entry_nonce(self):
        value = self.cleaned_data["entry_nonce"]
        try:
            return uuid.UUID(hex=value).hex
        except ValueError as error:
            raise forms.ValidationError("Reload the page and try again.") from error

    def clean_region(self):
        return self.cleaned_data["region"].strip().upper()
