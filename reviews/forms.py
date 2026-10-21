from django import forms

from .models import ProductReview


class ProductReviewForm(forms.Form):
    rating = forms.IntegerField(min_value=1, max_value=5, label="Rating")
    body = forms.CharField(
        min_length=20,
        max_length=2000,
        strip=True,
        label="Your review",
        widget=forms.Textarea(attrs={"rows": 6, "maxlength": 2000}),
    )


class ProductReviewModerationForm(forms.ModelForm):
    status = forms.ChoiceField(
        choices=(
            ("", "Choose a moderation action"),
            (ProductReview.Status.APPROVED, ProductReview.Status.APPROVED.label),
            (ProductReview.Status.REJECTED, ProductReview.Status.REJECTED.label),
            (ProductReview.Status.HIDDEN, ProductReview.Status.HIDDEN.label),
        )
    )

    class Meta:
        model = ProductReview
        fields = ("status", "moderation_reason")

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        reason = cleaned_data.get("moderation_reason", "")
        if not self.instance.pk or status is None:
            return cleaned_data

        current = ProductReview.objects.only("status", "moderation_reason").get(
            pk=self.instance.pk
        )
        if status == current.status and reason != current.moderation_reason:
            self.add_error(
                "moderation_reason",
                "A reason can only be changed when recording a moderation action.",
            )
        elif status != current.status and not reason.strip():
            self.add_error("moderation_reason", "Enter a reason for this moderation action.")
        return cleaned_data
