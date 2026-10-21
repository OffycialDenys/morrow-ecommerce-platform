from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone

from commerce.models import Order, OrderLine

from .models import ProductReview, ReviewModerationEvent, ReviewSubmissionEvent

MIN_REVIEW_LENGTH = 20
MAX_REVIEW_LENGTH = 2000


class ReviewError(ValueError):
    """A customer review cannot be submitted in the current order state."""


class ReviewRateLimited(ReviewError):
    """The customer has reached the review submission limit for the current hour."""


def _clean_review(rating: int, body: str) -> tuple[int, str]:
    if type(rating) is not int or not 1 <= rating <= 5:
        raise ReviewError("Choose a rating from one to five stars.")
    if not isinstance(body, str):
        raise ReviewError("Write a review before submitting.")
    body = body.strip()
    if not MIN_REVIEW_LENGTH <= len(body) <= MAX_REVIEW_LENGTH:
        raise ReviewError(
            f"Review text must contain between {MIN_REVIEW_LENGTH} and {MAX_REVIEW_LENGTH} characters."
        )
    return rating, body


def is_review_eligible(line: OrderLine, customer) -> bool:
    return (
        line.order.user_id == customer.pk
        and line.order.payment_status == Order.PaymentStatus.PAID
        and line.order.fulfillment_status == Order.FulfillmentStatus.DELIVERED
        and line.variant_id is not None
    )


def submit_review(customer, *, order_line_id: int, rating: int, body: str) -> ProductReview:
    rating, body = _clean_review(rating, body)
    if not getattr(customer, "is_authenticated", False) or customer.pk is None:
        raise ReviewError("Sign in with the account that placed the order.")

    now = timezone.now()
    window_start = now - timedelta(hours=1)
    rate_limit = getattr(settings, "REVIEW_RATE_LIMIT_HOURLY", 5)
    with transaction.atomic():
        customer = get_user_model().objects.select_for_update().get(pk=customer.pk)
        line = (
            OrderLine.objects.select_for_update()
            .select_related("order", "variant__product")
            .get(pk=order_line_id)
        )
        if not is_review_eligible(line, customer):
            raise ReviewError("Reviews are available after a delivered, paid purchase.")
        recent_submissions = ReviewSubmissionEvent.objects.filter(
            customer=customer, created_at__gte=window_start
        ).count()
        if recent_submissions >= rate_limit:
            raise ReviewRateLimited("You have reached the review limit. Try again later.")

        existing = ProductReview.objects.select_for_update().filter(order_line=line).first()
        if existing is not None:
            if existing.customer_id != customer.pk:
                raise ReviewError("This order line already has a different customer's review.")
            if existing.status == ProductReview.Status.HIDDEN:
                raise ReviewError("This review cannot be edited.")
            existing.rating = rating
            existing.body = body
            existing.status = ProductReview.Status.PENDING
            existing.moderation_reason = ""
            existing.moderated_by = None
            existing.moderated_at = None
            existing.save(
                update_fields=(
                    "rating",
                    "body",
                    "status",
                    "moderation_reason",
                    "moderated_by",
                    "moderated_at",
                    "updated_at",
                )
            )
            ReviewSubmissionEvent.objects.create(review=existing, customer=customer)
            return existing

        review = ProductReview.objects.create(
            product=line.variant.product,
            order_line=line,
            customer=customer,
            rating=rating,
            body=body,
        )
        ReviewSubmissionEvent.objects.create(review=review, customer=customer)
        return review


def moderate_review(review: ProductReview, *, status: str, moderator, reason: str):
    if moderator is None or not moderator.has_perm("reviews.moderate_review"):
        raise PermissionDenied("You do not have permission to moderate reviews.")
    if status not in {
        ProductReview.Status.APPROVED,
        ProductReview.Status.REJECTED,
        ProductReview.Status.HIDDEN,
    }:
        raise ReviewError("Choose an available moderation status.")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 240:
        raise ReviewError("A moderation reason is required.")

    with transaction.atomic():
        locked = ProductReview.objects.select_for_update().get(pk=review.pk)
        if locked.status == status:
            raise ReviewError("This review already has that moderation status.")
        previous_status = locked.status
        reason = reason.strip()
        event = ReviewModerationEvent.objects.create(
            review=locked,
            previous_status=previous_status,
            new_status=status,
            moderator=moderator,
            reason=reason,
        )
        locked.status = status
        locked.moderation_reason = reason
        locked.moderated_by = moderator
        locked.moderated_at = event.created_at
        locked.save(
            update_fields=(
                "status",
                "moderation_reason",
                "moderated_by",
                "moderated_at",
            )
        )
        return locked


def product_review_summary(product) -> dict[str, float | int | None]:
    summary = ProductReview.objects.filter(
        product=product, status=ProductReview.Status.APPROVED
    ).aggregate(count=Count("pk"), average=Avg("rating"))
    average = summary["average"]
    return {
        "count": summary["count"],
        "average": round(average, 1) if average is not None else None,
    }
