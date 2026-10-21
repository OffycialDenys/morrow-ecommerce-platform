import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductVariant
from commerce.models import Order
from commerce.services import create_order_snapshot
from reviews.models import ProductReview, ReviewSubmissionEvent
from reviews.services import (
    ReviewError,
    ReviewRateLimited,
    moderate_review,
    product_review_summary,
    submit_review,
)


class ProductReviewTests(TestCase):
    def setUp(self):
        self.customer = get_user_model().objects.create_user(
            "reviewer@example.com", "A-long-and-safe-passphrase-92!"
        )
        self.other_customer = get_user_model().objects.create_user(
            "other-reviewer@example.com", "Another-long-safe-passphrase-93!"
        )
        self.product = Product.objects.create(
            name="Hand-thrown Serving Bowl",
            slug="serving-bowl",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="BOWL-CREAM",
            price_minor=4200,
            available_units=4,
        )
        self.order = create_order_snapshot(
            self.customer,
            {"cart_items": {str(self.variant.pk): 1}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=0,
            tax_minor=0,
            currency="USD",
        )
        self.order.payment_status = Order.PaymentStatus.PAID
        self.order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        self.order.save(update_fields=("payment_status", "fulfillment_status"))
        self.order_line = self.order.lines.get()

    def test_unpaid_or_undelivered_orders_are_not_eligible(self):
        self.order.fulfillment_status = Order.FulfillmentStatus.PROCESSING
        self.order.save(update_fields=("fulfillment_status",))

        with self.assertRaises(ReviewError):
            submit_review(
                self.customer,
                order_line_id=self.order_line.pk,
                rating=5,
                body="Lovely handmade bowl with a beautiful glaze.",
            )

        self.order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("payment_status", "fulfillment_status"))
        with self.assertRaises(ReviewError):
            submit_review(
                self.customer,
                order_line_id=self.order_line.pk,
                rating=5,
                body="Lovely handmade bowl with a beautiful glaze.",
            )
        self.assertFalse(ProductReview.objects.exists())

    def test_verified_delivery_creates_a_pending_review(self):
        review = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=5,
            body="Lovely handmade bowl with a beautiful glaze.",
        )

        self.assertEqual(review.customer_id, self.customer.pk)
        self.assertEqual(review.product_id, self.product.pk)
        self.assertEqual(review.order_line_id, self.order_line.pk)
        self.assertEqual(review.status, ProductReview.Status.PENDING)

    def test_duplicate_review_updates_the_owners_review_and_requires_new_moderation(self):
        first = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=4,
            body="The bowl feels sturdy and has a soft, natural finish.",
        )
        admin_user = get_user_model().objects.create_superuser(
            "moderator@example.com", "Moderator-safe-passphrase-94!"
        )
        moderate_review(
            first,
            status=ProductReview.Status.APPROVED,
            moderator=admin_user,
            reason="Meets the review guidelines.",
        )

        updated = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=3,
            body="The finish is lovely, but the bowl is smaller than I expected.",
        )

        self.assertEqual(updated.pk, first.pk)
        self.assertEqual(updated.status, ProductReview.Status.PENDING)
        self.assertEqual(updated.rating, 3)
        self.assertEqual(updated.moderation_events.count(), 1)

    def test_another_customer_cannot_review_the_order_line(self):
        with self.assertRaises(ReviewError):
            submit_review(
                self.other_customer,
                order_line_id=self.order_line.pk,
                rating=5,
                body="Lovely handmade bowl with a beautiful glaze.",
            )

    @override_settings(REVIEW_RATE_LIMIT_HOURLY=2)
    def test_review_submissions_are_rate_limited_per_account(self):
        for body in (
            "The bowl feels sturdy and has a soft, natural finish.",
            "The glaze looks even better in person, and it is easy to clean.",
        ):
            submit_review(
                self.customer,
                order_line_id=self.order_line.pk,
                rating=4,
                body=body,
            )

        with self.assertRaises(ReviewRateLimited):
            submit_review(
                self.customer,
                order_line_id=self.order_line.pk,
                rating=5,
                body="This is another complete and useful review of the bowl.",
            )
        self.assertEqual(ReviewSubmissionEvent.objects.filter(customer=self.customer).count(), 2)

    def test_submission_and_moderation_events_are_immutable(self):
        review = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=5,
            body="Lovely handmade bowl with a beautiful glaze.",
        )
        submission = review.submission_events.get()
        with self.assertRaises(ValidationError):
            submission.save()
        with self.assertRaises(ValidationError):
            review.submission_events.delete()

        moderator = get_user_model().objects.create_superuser(
            "immutable-admin@example.com", "Immutable-admin-safe-passphrase-98!"
        )
        moderate_review(
            review,
            status=ProductReview.Status.APPROVED,
            moderator=moderator,
            reason="Approved after review.",
        )
        with self.assertRaises(ValidationError):
            review.moderation_events.update(reason="changed")

    def test_review_moderation_requires_permission_and_creates_audit_event(self):
        review = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=5,
            body="Lovely handmade bowl with a beautiful glaze.",
        )

        with self.assertRaises(PermissionDenied):
            moderate_review(
                review,
                status=ProductReview.Status.APPROVED,
                moderator=self.other_customer,
                reason="Approve it",
            )

        moderator = get_user_model().objects.create_superuser(
            "review-admin@example.com", "Review-admin-safe-passphrase-95!"
        )
        moderate_review(
            review,
            status=ProductReview.Status.APPROVED,
            moderator=moderator,
            reason="Verified delivery and meets policy.",
        )
        review.refresh_from_db()

        event = review.moderation_events.get()
        self.assertEqual(review.status, ProductReview.Status.APPROVED)
        self.assertEqual(event.previous_status, ProductReview.Status.PENDING)
        self.assertEqual(event.new_status, ProductReview.Status.APPROVED)
        self.assertEqual(event.moderator_id, moderator.pk)
        self.assertEqual(event.reason, "Verified delivery and meets policy.")

    def test_product_summary_uses_only_approved_reviews(self):
        review = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=5,
            body="Lovely handmade bowl with a beautiful glaze.",
        )

        self.assertEqual(product_review_summary(self.product), {"count": 0, "average": None})
        moderator = get_user_model().objects.create_superuser(
            "summary-admin@example.com", "Summary-admin-safe-passphrase-96!"
        )
        moderate_review(
            review,
            status=ProductReview.Status.APPROVED,
            moderator=moderator,
            reason="Approved.",
        )

        self.assertEqual(product_review_summary(self.product), {"count": 1, "average": 5.0})

    def test_customer_submission_form_cannot_set_moderation_state(self):
        self.client.force_login(self.customer)
        order_response = self.client.get(
            reverse("accounts:order-detail", args=[self.order.order_number])
        )
        self.assertContains(order_response, "Write a review")

        response = self.client.post(
            reverse("reviews:submit", args=[self.order_line.pk]),
            {
                "rating": 5,
                "body": "Lovely handmade bowl with a beautiful glaze.",
                "status": ProductReview.Status.APPROVED,
            },
        )

        self.assertRedirects(
            response, reverse("accounts:order-detail", args=[self.order.order_number])
        )
        self.assertEqual(ProductReview.objects.get().status, ProductReview.Status.PENDING)

    def test_admin_moderation_form_records_status_changes(self):
        review = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=5,
            body="Lovely handmade bowl with a beautiful glaze.",
        )
        moderator = get_user_model().objects.create_user(
            "staff-reviewer@example.com", "Staff-reviewer-safe-passphrase-99!", is_staff=True
        )
        permission = Permission.objects.get(
            content_type__app_label="reviews", codename="moderate_review"
        )
        moderator.user_permissions.add(permission)
        self.client.force_login(moderator)
        change_url = reverse("admin:reviews_productreview_change", args=[review.pk])
        inline_management = {
            "moderation_events-TOTAL_FORMS": "0",
            "moderation_events-INITIAL_FORMS": "0",
            "moderation_events-MIN_NUM_FORMS": "0",
            "moderation_events-MAX_NUM_FORMS": "1000",
        }

        invalid_response = self.client.post(
            change_url,
            {"status": ProductReview.Status.APPROVED, "moderation_reason": "", **inline_management},
        )
        self.assertEqual(invalid_response.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.status, ProductReview.Status.PENDING)
        self.assertEqual(review.moderation_events.count(), 0)

        response = self.client.post(
            change_url,
            {
                "status": ProductReview.Status.APPROVED,
                "moderation_reason": "Verified delivery and meets policy.",
                "_save": "Save",
                **inline_management,
            },
        )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, ProductReview.Status.APPROVED)
        self.assertEqual(review.moderation_events.count(), 1)

    def test_public_review_text_is_autoescaped(self):
        review = submit_review(
            self.customer,
            order_line_id=self.order_line.pk,
            rating=5,
            body="<script>alert('x')</script> This bowl is lovely and very sturdy.",
        )
        moderator = get_user_model().objects.create_superuser(
            "escape-admin@example.com", "Escape-admin-safe-passphrase-97!"
        )
        moderate_review(
            review,
            status=ProductReview.Status.APPROVED,
            moderator=moderator,
            reason="Approved after review.",
        )

        response = self.client.get(reverse("storefront:product-detail", args=[self.product.slug]))

        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert")
