"""Build a customer-facing export of records associated with one account."""

import json

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from commerce.models import Order
from promotions.models import GiveawayEntry, PointAdjustmentRequest, PointEntry
from reviews.models import ProductReview, ReviewAppeal, ReviewReport, ReviewSubmissionEvent

from .models import SavedProduct


def _encode(value):
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":"))


def _json_array(queryset, serialize):
    yield "["
    first = True
    for record in queryset.iterator(chunk_size=200):
        if not first:
            yield ","
        yield _encode(serialize(record))
        first = False
    yield "]"


def _serialize_saved_product(saved_product):
    return {
        "product_name": saved_product.product.name,
        "product_slug": saved_product.product.slug,
        "saved_at": saved_product.created_at,
    }


def _serialize_order(order):
    return {
        "order_number": str(order.order_number),
        "buyer_email": order.buyer_email,
        "currency": order.currency,
        "subtotal_minor": order.subtotal_minor,
        "shipping_minor": order.shipping_minor,
        "tax_minor": order.tax_minor,
        "total_minor": order.total_minor,
        "payment_status": order.payment_status,
        "fulfillment_status": order.fulfillment_status,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "payment_attempts": [
            {
                "provider": attempt.provider,
                "status": attempt.status,
                "amount_minor": attempt.amount_minor,
                "currency": attempt.currency,
                "created_at": attempt.created_at,
                "updated_at": attempt.updated_at,
            }
            for attempt in order.payment_attempts.all()
        ],
        "items": [
            {
                "product_name": line.product_name,
                "variant_label": line.variant_label,
                "sku": line.sku,
                "attributes": line.attributes,
                "quantity": line.quantity,
                "unit_price_minor": line.unit_price_minor,
                "line_total_minor": line.line_total_minor,
            }
            for line in order.lines.all()
        ],
    }


def _serialize_point_entry(entry):
    return {
        "event_type": entry.event_type,
        "points": entry.points,
        "reason": entry.reason,
        "order_number": str(entry.order.order_number) if entry.order_id else None,
        "order_item_sku": entry.order_line.sku if entry.order_line_id else None,
        "reverses_entry_id": entry.reversal_of_id,
        "created_at": entry.created_at,
    }


def _serialize_giveaway_entry(entry):
    return {
        "campaign_name": entry.campaign.name,
        "campaign_slug": entry.campaign.slug,
        "entry_method": entry.source,
        "ticket_number": entry.ticket_number,
        "region": entry.region,
        "created_at": entry.created_at,
    }


def _serialize_adjustment_request(request):
    return {
        "points": request.points,
        "reason": request.reason,
        "status": request.status,
        "created_at": request.created_at,
        "resolved_at": request.resolved_at,
    }


def _serialize_review(review):
    return {
        "product_name": review.product.name,
        "product_slug": review.product.slug,
        "rating": review.rating,
        "body": review.body,
        "status": review.status,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


def _serialize_review_submission(submission):
    return {
        "product_name": submission.review.product.name,
        "product_slug": submission.review.product.slug,
        "submitted_at": submission.created_at,
    }


def _serialize_review_report(report):
    return {
        "product_name": report.review.product.name,
        "product_slug": report.review.product.slug,
        "category": report.category,
        "details": report.details,
        "status": report.status,
        "created_at": report.created_at,
        "resolved_at": report.resolved_at,
    }


def _serialize_review_appeal(appeal):
    return {
        "product_name": appeal.review.product.name,
        "product_slug": appeal.review.product.slug,
        "reason": appeal.reason,
        "status": appeal.status,
        "created_at": appeal.created_at,
        "resolved_at": appeal.resolved_at,
    }


def stream_account_export(user_id):
    """Yield a versioned JSON export without buffering all account history in memory."""
    user = (
        get_user_model()
        .objects.only("email", "first_name", "last_name", "date_joined", "last_login")
        .get(pk=user_id)
    )
    profile = {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "date_joined": user.date_joined,
        "last_login": user.last_login,
    }

    yield '{"schema_version":1,"exported_at":'
    yield _encode(timezone.now())
    yield ',"account":'
    yield _encode(profile)

    collections = (
        (
            "saved_products",
            SavedProduct.objects.filter(user_id=user_id).select_related("product").order_by("pk"),
            _serialize_saved_product,
        ),
        (
            "orders",
            Order.objects.filter(user_id=user_id)
            .prefetch_related("lines", "payment_attempts")
            .order_by("pk"),
            _serialize_order,
        ),
        (
            "points_ledger",
            PointEntry.objects.filter(customer_id=user_id)
            .select_related("order", "order_line")
            .order_by("pk"),
            _serialize_point_entry,
        ),
        (
            "giveaway_entries",
            GiveawayEntry.objects.filter(customer_id=user_id)
            .select_related("campaign")
            .order_by("pk"),
            _serialize_giveaway_entry,
        ),
        (
            "point_adjustment_requests",
            PointAdjustmentRequest.objects.filter(customer_id=user_id).order_by("pk"),
            _serialize_adjustment_request,
        ),
        (
            "reviews",
            ProductReview.objects.filter(customer_id=user_id)
            .select_related("product")
            .order_by("pk"),
            _serialize_review,
        ),
        (
            "review_submissions",
            ReviewSubmissionEvent.objects.filter(customer_id=user_id)
            .select_related("review__product")
            .order_by("pk"),
            _serialize_review_submission,
        ),
        (
            "review_reports",
            ReviewReport.objects.filter(reporter_id=user_id)
            .select_related("review__product")
            .order_by("pk"),
            _serialize_review_report,
        ),
        (
            "review_appeals",
            ReviewAppeal.objects.filter(customer_id=user_id)
            .select_related("review__product")
            .order_by("pk"),
            _serialize_review_appeal,
        ),
    )

    for name, queryset, serializer in collections:
        yield ","
        yield _encode(name)
        yield ":"
        yield from _json_array(queryset, serializer)
    yield "}"
