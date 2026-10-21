import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductVariant
from commerce.models import Order
from commerce.services import create_order_snapshot
from reviews.models import (
    ProductReview,
    ReviewAppeal,
    ReviewAppealEvent,
    ReviewModerationEvent,
    ReviewReport,
    ReviewReportEvent,
    ReviewSubmissionEvent,
)
from reviews.services import (
    ReviewError,
    ReviewRateLimited,
    ReviewReportRateLimited,
    appeal_review,
    moderate_review,
    product_review_summary,
    report_review,
    resolve_review_appeal,
    resolve_review_report,
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

    def _new_delivered_order_line(self):
        order = create_order_snapshot(
            self.customer,
            {"cart_items": {str(self.variant.pk): 1}},
            idempotency_key=uuid.uuid4(),
            shipping_minor=0,
            tax_minor=0,
            currency="USD",
        )
        order.payment_status = Order.PaymentStatus.PAID
        order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        order.save(update_fields=("payment_status", "fulfillment_status"))
        return order.lines.get()

    def _make_moderated_review(self, status, *, order_line=None):
        order_line = order_line or self.order_line
        review = submit_review(
            self.customer,
            order_line_id=order_line.pk,
            rating=5,
            body="Lovely handmade bowl with a beautiful glaze.",
        )
        moderator = get_user_model().objects.create_superuser(
            f"moderator-{status}@example.com", "Moderator-safe-passphrase-111!"
        )
        moderate_review(
            review,
            status=status,
            moderator=moderator,
            reason="Reviewed against the store policy.",
        )
        review.refresh_from_db()
        return review, moderator

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

    def test_customer_can_report_another_customers_published_review_once(self):
        review, _moderator = self._make_moderated_review(ProductReview.Status.APPROVED)

        report = report_review(
            self.other_customer,
            review_id=review.pk,
            category=ReviewReport.Category.SPAM,
            details="This review includes an unrelated promotional link.",
        )

        self.assertEqual(report.status, ReviewReport.Status.OPEN)
        self.assertEqual(report.reporter_id, self.other_customer.pk)
        self.assertEqual(report.details, "This review includes an unrelated promotional link.")
        with self.assertRaises(ReviewError):
            report_review(
                self.other_customer,
                review_id=review.pk,
                category=ReviewReport.Category.SPAM,
            )
        with self.assertRaises(ReviewError):
            report_review(
                self.customer,
                review_id=review.pk,
                category=ReviewReport.Category.SPAM,
            )

    @override_settings(REVIEW_REPORT_RATE_LIMIT_HOURLY=2)
    def test_review_reports_are_rate_limited_per_account(self):
        moderator = get_user_model().objects.create_superuser(
            "report-limit-moderator@example.com", "Moderator-safe-passphrase-114!"
        )
        reviews = []
        for index in range(3):
            line = self.order_line if index == 0 else self._new_delivered_order_line()
            review = submit_review(
                self.customer,
                order_line_id=line.pk,
                rating=5,
                body="Lovely handmade bowl with a beautiful glaze.",
            )
            moderate_review(
                review,
                status=ProductReview.Status.APPROVED,
                moderator=moderator,
                reason="Reviewed against the store policy.",
            )
            reviews.append(review)

        for review in reviews[:2]:
            report_review(
                self.other_customer,
                review_id=review.pk,
                category=ReviewReport.Category.SPAM,
            )
        with self.assertRaises(ReviewReportRateLimited):
            report_review(
                self.other_customer,
                review_id=reviews[2].pk,
                category=ReviewReport.Category.SPAM,
            )
        self.assertEqual(ReviewReport.objects.filter(reporter=self.other_customer).count(), 2)

        self.client.force_login(self.other_customer)
        response = self.client.post(
            reverse("reviews:report", args=[reviews[2].pk]),
            {"category": ReviewReport.Category.SPAM, "details": "Repeated promotional material."},
        )
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "report limit", status_code=429)

    def test_report_action_requires_review_moderation_and_leaves_audit_event(self):
        review, moderator = self._make_moderated_review(ProductReview.Status.APPROVED)
        report = report_review(
            self.other_customer,
            review_id=review.pk,
            category=ReviewReport.Category.INAPPROPRIATE,
        )

        with self.assertRaises(PermissionDenied):
            resolve_review_report(
                report,
                status=ReviewReport.Status.DISMISSED,
                moderator=self.other_customer,
                reason="Not a policy violation.",
            )
        with self.assertRaises(ReviewError):
            resolve_review_report(
                report,
                status=ReviewReport.Status.ACTION_TAKEN,
                moderator=moderator,
                reason="The reported content was removed.",
            )

        moderate_review(
            review,
            status=ProductReview.Status.HIDDEN,
            moderator=moderator,
            reason="Removed after investigating a customer report.",
        )
        resolved = resolve_review_report(
            report,
            status=ReviewReport.Status.ACTION_TAKEN,
            moderator=moderator,
            reason="The review was hidden after investigation.",
        )

        self.assertEqual(resolved.status, ReviewReport.Status.ACTION_TAKEN)
        event = ReviewReportEvent.objects.get(report=report)
        self.assertEqual(event.previous_status, ReviewReport.Status.OPEN)
        self.assertEqual(event.moderator_id, moderator.pk)
        with self.assertRaises(ValidationError):
            event.save()

    def test_review_owner_can_appeal_a_rejected_review_and_moderator_can_reopen_it(self):
        review, moderator = self._make_moderated_review(ProductReview.Status.REJECTED)
        reason = "The note seems to describe a product-specific concern unrelated to the policy."
        appeal = appeal_review(self.customer, review_id=review.pk, reason=reason)

        self.assertEqual(appeal.status, ReviewAppeal.Status.PENDING)
        self.assertEqual(appeal.reason, reason)
        with self.assertRaises(ReviewError):
            submit_review(
                self.customer,
                order_line_id=self.order_line.pk,
                rating=4,
                body="The glaze has a softer tone than I expected, but it is sturdy.",
            )
        with self.assertRaises(ReviewError):
            appeal_review(self.other_customer, review_id=review.pk, reason=reason)
        with self.assertRaises(ReviewError):
            appeal_review(self.customer, review_id=review.pk, reason=reason)

        resolved = resolve_review_appeal(
            appeal,
            status=ReviewAppeal.Status.REOPENED,
            moderator=moderator,
            reason="The moderation decision will be reviewed again.",
        )
        review.refresh_from_db()
        self.assertEqual(resolved.status, ReviewAppeal.Status.REOPENED)
        self.assertEqual(review.status, ProductReview.Status.PENDING)
        self.assertIsNone(review.moderated_by)
        self.assertEqual(ReviewAppealEvent.objects.get(appeal=appeal).moderator_id, moderator.pk)
        moderation_event = ReviewModerationEvent.objects.get(appeal=appeal)
        self.assertEqual(moderation_event.previous_status, ProductReview.Status.REJECTED)
        self.assertEqual(moderation_event.new_status, ProductReview.Status.PENDING)

    def test_customer_can_submit_report_and_appeal_from_customer_pages(self):
        approved, _moderator = self._make_moderated_review(ProductReview.Status.APPROVED)
        self.client.force_login(self.other_customer)
        detail_response = self.client.get(
            reverse("storefront:product-detail", args=[self.product.slug])
        )
        self.assertContains(detail_response, reverse("reviews:report", args=[approved.pk]))
        report_response = self.client.post(
            reverse("reviews:report", args=[approved.pk]),
            {
                "category": ReviewReport.Category.IRRELEVANT,
                "details": "The text does not appear to describe this bowl.",
            },
        )
        self.assertRedirects(
            report_response,
            reverse("storefront:product-detail", args=[self.product.slug]),
        )
        self.assertTrue(
            ReviewReport.objects.filter(review=approved, reporter=self.other_customer).exists()
        )

        appeal_line = self._new_delivered_order_line()
        appeal_review_obj, _moderator = self._make_moderated_review(
            ProductReview.Status.REJECTED, order_line=appeal_line
        )
        self.client.force_login(self.customer)
        order_response = self.client.get(
            reverse("accounts:order-detail", args=[appeal_line.order.order_number])
        )
        self.assertContains(order_response, reverse("reviews:appeal", args=[appeal_review_obj.pk]))
        appeal_response = self.client.post(
            reverse("reviews:appeal", args=[appeal_review_obj.pk]),
            {"reason": "I believe the decision did not account for the product-specific context."},
        )
        self.assertRedirects(
            appeal_response,
            reverse("accounts:order-detail", args=[appeal_line.order.order_number]),
        )
        self.assertTrue(ReviewAppeal.objects.filter(review=appeal_review_obj).exists())
        edit_response = self.client.get(reverse("reviews:submit", args=[appeal_line.pk]))
        self.assertRedirects(
            edit_response,
            reverse("accounts:order-detail", args=[appeal_line.order.order_number]),
        )

    def test_admin_can_resolve_report_and_appeal_with_audit_records(self):
        moderator = get_user_model().objects.create_user(
            "review-queue@example.com", "Review-queue-safe-passphrase-112!", is_staff=True
        )
        permission = Permission.objects.get(
            content_type__app_label="reviews", codename="moderate_review"
        )
        moderator.user_permissions.add(permission)

        approved, _owner = self._make_moderated_review(ProductReview.Status.APPROVED)
        report = report_review(
            self.other_customer,
            review_id=approved.pk,
            category=ReviewReport.Category.SPAM,
        )
        self.client.force_login(moderator)
        event_forms = {
            "events-TOTAL_FORMS": "0",
            "events-INITIAL_FORMS": "0",
            "events-MIN_NUM_FORMS": "0",
            "events-MAX_NUM_FORMS": "1000",
        }
        report_response = self.client.post(
            reverse("admin:reviews_reviewreport_change", args=[report.pk]),
            {
                "status": ReviewReport.Status.DISMISSED,
                "resolution_note": "The reported text is relevant to the product.",
                "_save": "Save",
                **event_forms,
            },
        )
        self.assertEqual(report_response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, ReviewReport.Status.DISMISSED)
        self.assertEqual(ReviewReportEvent.objects.filter(report=report).count(), 1)

        rejected, _owner = self._make_moderated_review(
            ProductReview.Status.REJECTED, order_line=self._new_delivered_order_line()
        )
        appeal = appeal_review(
            self.customer,
            review_id=rejected.pk,
            reason="The review explains a real issue I experienced with this exact product.",
        )
        appeal_response = self.client.post(
            reverse("admin:reviews_reviewappeal_change", args=[appeal.pk]),
            {
                "status": ReviewAppeal.Status.REOPENED,
                "resolution_note": "The original review will be reconsidered.",
                "_save": "Save",
                **event_forms,
            },
        )
        self.assertEqual(appeal_response.status_code, 302)
        appeal.refresh_from_db()
        rejected.refresh_from_db()
        self.assertEqual(appeal.status, ReviewAppeal.Status.REOPENED)
        self.assertEqual(rejected.status, ProductReview.Status.PENDING)
        self.assertEqual(ReviewAppealEvent.objects.filter(appeal=appeal).count(), 1)
