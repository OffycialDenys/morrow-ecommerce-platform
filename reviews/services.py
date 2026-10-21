from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count
from django.utils import timezone

from commerce.models import Order, OrderLine

from .models import (
    ProductReview,
    ReviewAppeal,
    ReviewAppealEvent,
    ReviewModerationEvent,
    ReviewReport,
    ReviewReportEvent,
    ReviewSubmissionEvent,
)

MIN_REVIEW_LENGTH = 20
MAX_REVIEW_LENGTH = 2000


class ReviewError(ValueError):
    """A customer review cannot be submitted in the current order state."""


class ReviewRateLimited(ReviewError):
    """The customer has reached the review submission limit for the current hour."""


def _clean_review(rating: int, body: str) -> tuple[int, str]:
    if type(rating) is not int or not 1 <= rating <= 5:  # noqa: E721
        raise ReviewError("Choose a rating from one to five stars.")
    if not isinstance(body, str):
        raise ReviewError("Write a review before submitting.")
    body = body.strip()
    if not MIN_REVIEW_LENGTH <= len(body) <= MAX_REVIEW_LENGTH:
        raise ReviewError(
            f"Review text must contain between {MIN_REVIEW_LENGTH} and {MAX_REVIEW_LENGTH} characters."
        )
    return rating, body


def _clean_resolution_reason(reason: str) -> str:
    if not isinstance(reason, str):
        raise ReviewError("A resolution reason is required.")
    cleaned = reason.strip()
    if (
        not cleaned
        or len(cleaned) > 240
        or any(ord(character) < 32 and character not in "\n\r\t" for character in cleaned)
    ):
        raise ReviewError("The resolution reason is invalid.")
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ReviewError("The resolution reason is invalid.") from error
    return cleaned


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
            if (
                existing.status == ProductReview.Status.REJECTED
                and ReviewAppeal.objects.filter(
                    review=existing, status=ReviewAppeal.Status.PENDING
                ).exists()
            ):
                raise ReviewError("Wait for the pending appeal before editing this review.")
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


def report_review(reporter, *, review_id: int, category: str, details: str = "") -> ReviewReport:
    if not getattr(reporter, "is_authenticated", False) or reporter.pk is None:
        raise ReviewError("Sign in before reporting a review.")
    if category not in ReviewReport.Category.values:
        raise ReviewError("Choose an available report reason.")
    if not isinstance(details, str):
        raise ReviewError("Additional details must be text.")
    details = details.strip()
    if len(details) > 1000:
        raise ReviewError("Additional details are too long.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in details):
        raise ReviewError("Additional details contain unsupported characters.")
    try:
        details.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ReviewError("Additional details contain unsupported characters.") from error

    try:
        with transaction.atomic():
            review = (
                ProductReview.objects.select_for_update()
                .select_related("customer")
                .filter(pk=review_id, status=ProductReview.Status.APPROVED)
                .first()
            )
            if review is None:
                raise ReviewError("This review is not available to report.")
            if review.customer_id == reporter.pk:
                raise ReviewError("You cannot report your own review.")
            return ReviewReport.objects.create(
                review=review,
                reporter=reporter,
                category=category,
                details=details,
            )
    except IntegrityError as error:
        raise ReviewError("You have already reported this review.") from error


def appeal_review(customer, *, review_id: int, reason: str) -> ReviewAppeal:
    if not getattr(customer, "is_authenticated", False) or customer.pk is None:
        raise ReviewError("Sign in with the account that wrote the review.")
    if not isinstance(reason, str):
        raise ReviewError("Explain why you are appealing this decision.")
    reason = reason.strip()
    if not 30 <= len(reason) <= 1200:
        raise ReviewError("An appeal must contain between 30 and 1,200 characters.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in reason):
        raise ReviewError("The appeal contains unsupported characters.")
    try:
        reason.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ReviewError("The appeal contains unsupported characters.") from error

    try:
        with transaction.atomic():
            review = (
                ProductReview.objects.select_for_update()
                .filter(pk=review_id, customer=customer)
                .first()
            )
            if review is None:
                raise ReviewError("This review cannot be appealed from this account.")
            if review.status not in {ProductReview.Status.REJECTED, ProductReview.Status.HIDDEN}:
                raise ReviewError("Only a rejected or hidden review can be appealed.")
            return ReviewAppeal.objects.create(
                review=review,
                customer=customer,
                reason=reason,
            )
    except IntegrityError as error:
        raise ReviewError("An appeal has already been submitted for this review.") from error


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


def resolve_review_report(
    report: ReviewReport, *, status: str, moderator, reason: str
) -> ReviewReport:
    if moderator is None or not moderator.has_perm("reviews.moderate_review"):
        raise PermissionDenied("You do not have permission to resolve review reports.")
    if status not in {ReviewReport.Status.ACTION_TAKEN, ReviewReport.Status.DISMISSED}:
        raise ReviewError("Choose an available report outcome.")
    reason = _clean_resolution_reason(reason)

    with transaction.atomic():
        locked = ReviewReport.objects.select_for_update().select_related("review").get(pk=report.pk)
        if locked.status != ReviewReport.Status.OPEN:
            raise ReviewError("This report has already been resolved.")
        if status == ReviewReport.Status.ACTION_TAKEN and (
            locked.review.status == ProductReview.Status.APPROVED
        ):
            raise ReviewError("Moderate the reported review before recording action taken.")

        event = ReviewReportEvent.objects.create(
            report=locked,
            previous_status=locked.status,
            new_status=status,
            moderator=moderator,
            reason=reason,
        )
        locked.status = status
        locked.resolution_note = reason
        locked.resolved_by = moderator
        locked.resolved_at = event.created_at
        locked.save(update_fields=("status", "resolution_note", "resolved_by", "resolved_at"))
        return locked


def resolve_review_appeal(
    appeal: ReviewAppeal, *, status: str, moderator, reason: str
) -> ReviewAppeal:
    if moderator is None or not moderator.has_perm("reviews.moderate_review"):
        raise PermissionDenied("You do not have permission to resolve review appeals.")
    if status not in {ReviewAppeal.Status.REOPENED, ReviewAppeal.Status.UPHELD}:
        raise ReviewError("Choose an available appeal outcome.")
    reason = _clean_resolution_reason(reason)

    with transaction.atomic():
        locked = ReviewAppeal.objects.select_for_update().select_related("review").get(pk=appeal.pk)
        review = ProductReview.objects.select_for_update().get(pk=locked.review_id)
        if locked.status != ReviewAppeal.Status.PENDING:
            raise ReviewError("This appeal has already been resolved.")
        if review.status not in {ProductReview.Status.REJECTED, ProductReview.Status.HIDDEN}:
            raise ReviewError("This review's moderation decision cannot be appealed.")

        if status == ReviewAppeal.Status.REOPENED:
            ReviewModerationEvent.objects.create(
                review=review,
                appeal=locked,
                previous_status=review.status,
                new_status=ProductReview.Status.PENDING,
                moderator=moderator,
                reason=reason,
            )
            review.status = ProductReview.Status.PENDING
            review.moderation_reason = ""
            review.moderated_by = None
            review.moderated_at = None
            review.save(
                update_fields=(
                    "status",
                    "moderation_reason",
                    "moderated_by",
                    "moderated_at",
                    "updated_at",
                )
            )

        event = ReviewAppealEvent.objects.create(
            appeal=locked,
            previous_status=locked.status,
            new_status=status,
            moderator=moderator,
            reason=reason,
        )
        locked.status = status
        locked.resolution_note = reason
        locked.resolved_by = moderator
        locked.resolved_at = event.created_at
        locked.save(update_fields=("status", "resolution_note", "resolved_by", "resolved_at"))
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
