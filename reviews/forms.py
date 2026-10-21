from django import forms

from .models import ProductReview, ReviewAppeal, ReviewReport


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

        current = ProductReview.objects.only("status", "moderation_reason").get(pk=self.instance.pk)
        if status == current.status and reason != current.moderation_reason:
            self.add_error(
                "moderation_reason",
                "A reason can only be changed when recording a moderation action.",
            )
        elif status != current.status and not reason.strip():
            self.add_error("moderation_reason", "Enter a reason for this moderation action.")
        return cleaned_data


class ReviewReportForm(forms.Form):
    category = forms.ChoiceField(choices=ReviewReport.Category.choices, label="Reason")
    details = forms.CharField(
        required=False,
        max_length=1000,
        strip=True,
        label="Additional details",
        widget=forms.Textarea(attrs={"rows": 5, "maxlength": 1000}),
    )


class ReviewAppealForm(forms.Form):
    reason = forms.CharField(
        min_length=30,
        max_length=1200,
        strip=True,
        label="Why should we review this decision?",
        widget=forms.Textarea(attrs={"rows": 6, "maxlength": 1200}),
    )


class ReviewReportResolutionForm(forms.ModelForm):
    status = forms.ChoiceField(
        choices=(
            (ReviewReport.Status.OPEN, "Choose an outcome"),
            (ReviewReport.Status.ACTION_TAKEN, ReviewReport.Status.ACTION_TAKEN.label),
            (ReviewReport.Status.DISMISSED, ReviewReport.Status.DISMISSED.label),
        )
    )

    class Meta:
        model = ReviewReport
        fields = ("status", "resolution_note")

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        note = cleaned_data.get("resolution_note", "")
        if status == ReviewReport.Status.OPEN:
            self.add_error("status", "Choose how this report was resolved.")
        elif (
            status == ReviewReport.Status.ACTION_TAKEN
            and self.instance.pk
            and self.instance.review.status == ProductReview.Status.APPROVED
        ):
            self.add_error("status", "Moderate the reported review before recording action taken.")
        if (
            status in {ReviewReport.Status.ACTION_TAKEN, ReviewReport.Status.DISMISSED}
            and not note.strip()
        ):
            self.add_error("resolution_note", "Enter a reason for resolving this report.")
        return cleaned_data


class ReviewAppealResolutionForm(forms.ModelForm):
    status = forms.ChoiceField(
        choices=(
            (ReviewAppeal.Status.PENDING, "Choose an outcome"),
            (ReviewAppeal.Status.REOPENED, ReviewAppeal.Status.REOPENED.label),
            (ReviewAppeal.Status.UPHELD, ReviewAppeal.Status.UPHELD.label),
        )
    )

    class Meta:
        model = ReviewAppeal
        fields = ("status", "resolution_note")

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        note = cleaned_data.get("resolution_note", "")
        if status == ReviewAppeal.Status.PENDING:
            self.add_error("status", "Choose how this appeal was resolved.")
        elif self.instance.pk and (
            self.instance.status != ReviewAppeal.Status.PENDING
            or self.instance.review.status
            not in {ProductReview.Status.REJECTED, ProductReview.Status.HIDDEN}
        ):
            self.add_error("status", "This appeal is no longer available to resolve.")
        if (
            status in {ReviewAppeal.Status.REOPENED, ReviewAppeal.Status.UPHELD}
            and not note.strip()
        ):
            self.add_error("resolution_note", "Enter a reason for resolving this appeal.")
        return cleaned_data
