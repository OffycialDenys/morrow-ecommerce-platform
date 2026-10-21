from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from catalog.models import Product
from commerce.models import OrderLine


class ImmutableReviewEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Review event history cannot be changed.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Review event history cannot be changed.")

    def delete(self):
        raise ValidationError("Review event history cannot be deleted.")


class ProductReview(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending moderation"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        HIDDEN = "hidden", "Hidden"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="reviews")
    order_line = models.OneToOneField(
        OrderLine, on_delete=models.PROTECT, related_name="product_review"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="product_reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=(MinValueValidator(1), MaxValueValidator(5))
    )
    body = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    moderation_reason = models.CharField(max_length=240, blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="moderated_product_reviews",
        null=True,
        blank=True,
    )
    moderated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=1, rating__lte=5),
                name="reviews_rating_1_through_5",
            ),
        ]
        indexes = [
            models.Index(fields=("product", "status", "-created_at"), name="reviews_prod_public_idx"),
            models.Index(fields=("customer", "-updated_at"), name="reviews_customer_recent_idx"),
        ]
        permissions = [
            ("moderate_review", "Can moderate customer reviews"),
        ]

    def __str__(self) -> str:
        return f"{self.rating}/5 review of {self.product}"


class ReviewModerationEvent(models.Model):
    review = models.ForeignKey(
        ProductReview, on_delete=models.PROTECT, related_name="moderation_events"
    )
    previous_status = models.CharField(max_length=12, choices=ProductReview.Status.choices)
    new_status = models.CharField(max_length=12, choices=ProductReview.Status.choices)
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="review_moderation_events",
    )
    reason = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")

    objects = ImmutableReviewEventQuerySet.as_manager()

    def __str__(self) -> str:
        return f"{self.previous_status} → {self.new_status} for {self.review}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Review moderation history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review moderation history cannot be deleted.")


class ReviewSubmissionEvent(models.Model):
    review = models.ForeignKey(
        ProductReview, on_delete=models.PROTECT, related_name="submission_events"
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="review_submissions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableReviewEventQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("customer", "-created_at"), name="reviews_submit_customer_idx"),
        ]

    def __str__(self) -> str:
        return f"Submission for {self.review} by {self.customer}"

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Review submission history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review submission history cannot be deleted.")
