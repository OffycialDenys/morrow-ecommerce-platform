from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import (
    Product,
    SupplierCatalogSnapshot,
    SupplierSyncItem,
    SupplierSyncRun,
)
from integrations.suppliers import (
    SupplierAdapterError,
    SupplierImportDisabled,
    SupplierProductData,
    SupplierSyncAlreadyRunning,
    SupplierVariantData,
    run_supplier_sync,
)


class FixtureSupplierAdapter:
    supplier_code = "aliexpress"

    def __init__(self, products=(), *, failure=None):
        self.products = tuple(products)
        self.failure = failure
        self.requested_limit = None
        self.iterated = False

    def iter_products(self, *, limit):
        self.requested_limit = limit
        self.iterated = True
        for product in self.products:
            yield product
        if self.failure is not None:
            raise self.failure


@override_settings(SUPPLIER_SYNC_ENABLED=True)
class SupplierSyncTests(TestCase):
    def setUp(self):
        self.requester = get_user_model().objects.create_user(
            "supplier-operator@example.com",
            "Supplier-operator-safe-passphrase-23!",
            is_staff=True,
        )
        permission = Permission.objects.get(
            content_type__app_label="catalog", codename="run_supplier_sync"
        )
        self.requester.user_permissions.add(permission)

    def make_product(
        self,
        *,
        product_id="ae-100",
        name="Stoneware cup",
        image_url="https://images.example.test/cup.webp",
        supplier_price_minor=1200,
    ):
        return SupplierProductData(
            supplier_product_id=product_id,
            name=name,
            description="A plain supplier description.",
            image_url=image_url,
            variants=(
                SupplierVariantData(
                    supplier_variant_id=f"{product_id}-oat",
                    supplier_price_minor=supplier_price_minor,
                    currency="usd",
                    sku="CUP-OAT",
                    label="Oat",
                    attributes={"color": "Oat"},
                ),
            ),
        )

    def test_batch_is_staged_for_review_without_modifying_live_catalog(self):
        product = self.make_product()
        adapter = FixtureSupplierAdapter((product, product))

        run = run_supplier_sync(adapter, requester=self.requester)

        self.assertEqual(run.status, SupplierSyncRun.Status.SUCCEEDED)
        self.assertEqual(run.records_seen, 2)
        self.assertEqual(run.staged_count, 1)
        self.assertEqual(run.skipped_count, 1)
        self.assertEqual(run.failed_count, 0)
        self.assertEqual(adapter.requested_limit, 100)
        self.assertEqual(SupplierCatalogSnapshot.objects.count(), 1)
        self.assertEqual(SupplierCatalogSnapshot.objects.get().variants[0]["currency"], "USD")
        self.assertEqual(SupplierCatalogSnapshot.objects.get().sync_run_id, run.pk)
        self.assertEqual(
            list(run.items.order_by("record_number").values_list("outcome", flat=True)),
            [SupplierSyncItem.Outcome.STAGED, SupplierSyncItem.Outcome.DUPLICATE],
        )
        self.assertEqual(run.items.filter(snapshot__isnull=False).count(), 1)
        self.assertFalse(run.items.filter(error_message__contains="supplier description").exists())

        self.assertEqual(Product.objects.count(), 0)

    def test_dry_run_validates_records_without_persisting_product_snapshots(self):
        adapter = FixtureSupplierAdapter((self.make_product(),))

        run = run_supplier_sync(adapter, requester=self.requester, dry_run=True)

        self.assertEqual(run.status, SupplierSyncRun.Status.SUCCEEDED)
        self.assertEqual(run.validated_count, 1)
        self.assertEqual(run.staged_count, 0)
        self.assertEqual(run.items.get().outcome, SupplierSyncItem.Outcome.VALIDATED)
        self.assertFalse(SupplierCatalogSnapshot.objects.exists())

    def test_invalid_record_is_reported_and_later_records_are_processed(self):
        invalid = self.make_product(product_id="bad-image", image_url="http://example.test/a.png")
        valid = self.make_product(product_id="valid-after-error")
        adapter = FixtureSupplierAdapter((invalid, valid))

        run = run_supplier_sync(adapter, requester=self.requester)

        self.assertEqual(run.status, SupplierSyncRun.Status.PARTIAL)
        self.assertEqual(run.records_seen, 2)
        self.assertEqual(run.staged_count, 1)
        self.assertEqual(run.failed_count, 1)
        failed = run.items.get(outcome=SupplierSyncItem.Outcome.FAILED)
        self.assertEqual(failed.error_code, "invalid_image_url")
        self.assertIn("HTTPS", failed.error_message)
        self.assertEqual(SupplierCatalogSnapshot.objects.count(), 1)
        self.assertEqual(SupplierCatalogSnapshot.objects.get().supplier_product_id, "valid-after-error")

    def test_conflicting_duplicate_product_does_not_replace_first_snapshot(self):
        first = self.make_product(name="First title")
        conflicting = self.make_product(name="Different title")

        run = run_supplier_sync(
            FixtureSupplierAdapter((first, conflicting)), requester=self.requester
        )

        self.assertEqual(run.status, SupplierSyncRun.Status.PARTIAL)
        self.assertEqual(run.staged_count, 1)
        self.assertEqual(run.failed_count, 1)
        self.assertEqual(
            run.items.get(outcome=SupplierSyncItem.Outcome.FAILED).error_code,
            "duplicate_conflict",
        )
        self.assertEqual(SupplierCatalogSnapshot.objects.count(), 1)
        self.assertEqual(SupplierCatalogSnapshot.objects.get().name, "First title")

    def test_import_batch_limit_is_enforced_even_if_adapter_exceeds_it(self):
        products = tuple(self.make_product(product_id=f"ae-{number}") for number in range(3))

        run = run_supplier_sync(
            FixtureSupplierAdapter(products), requester=self.requester, limit=1
        )

        self.assertEqual(run.status, SupplierSyncRun.Status.PARTIAL)
        self.assertEqual(run.records_seen, 1)
        self.assertEqual(run.staged_count, 1)
        self.assertIn("batch limit", run.error_summary)
        self.assertEqual(SupplierCatalogSnapshot.objects.count(), 1)

    def test_supplier_record_size_is_bounded(self):
        attributes = {f"attribute-{index}": "value" * 40 for index in range(20)}
        variants = tuple(
            SupplierVariantData(
                supplier_variant_id=f"large-{index}",
                supplier_price_minor=100,
                currency="USD",
                attributes=attributes,
            )
            for index in range(200)
        )
        product = SupplierProductData(
            supplier_product_id="large-product",
            name="Large product",
            variants=variants,
        )

        run = run_supplier_sync(
            FixtureSupplierAdapter((product,)), requester=self.requester
        )

        self.assertEqual(run.status, SupplierSyncRun.Status.PARTIAL)
        self.assertEqual(run.failed_count, 1)
        self.assertEqual(run.items.get().error_code, "record_too_large")
        self.assertFalse(SupplierCatalogSnapshot.objects.exists())

    def test_unencodable_supplier_text_fails_only_its_record(self):
        invalid = self.make_product(product_id="bad-unicode", name="Cup \ud800")
        valid = self.make_product(product_id="valid-after-unicode")

        run = run_supplier_sync(
            FixtureSupplierAdapter((invalid, valid)), requester=self.requester
        )

        self.assertEqual(run.status, SupplierSyncRun.Status.PARTIAL)
        self.assertEqual(run.records_seen, 2)
        self.assertEqual(run.failed_count, 1)
        self.assertEqual(run.staged_count, 1)
        self.assertEqual(
            run.items.get(outcome=SupplierSyncItem.Outcome.FAILED).error_code,
            "invalid_text",
        )

    def test_provider_failure_is_recorded_without_leaking_provider_message(self):
        adapter = FixtureSupplierAdapter(
            (self.make_product(),), failure=SupplierAdapterError("rate_limited")
        )

        run = run_supplier_sync(adapter, requester=self.requester)

        self.assertEqual(run.status, SupplierSyncRun.Status.PARTIAL)
        self.assertEqual(run.records_seen, 1)
        self.assertEqual(run.error_summary, "The supplier service temporarily limited requests.")

    def test_stalled_run_is_marked_failed_before_a_new_run_starts(self):
        stalled = SupplierSyncRun.objects.create(
            supplier_code="aliexpress",
            requested_by=self.requester,
            started_at=timezone.now() - timedelta(minutes=5),
            last_progress_at=timezone.now() - timedelta(minutes=5),
        )
        adapter = FixtureSupplierAdapter((self.make_product(),))

        with override_settings(SUPPLIER_SYNC_STALE_AFTER_SECONDS=60):
            run = run_supplier_sync(adapter, requester=self.requester)

        stalled.refresh_from_db()
        self.assertEqual(stalled.status, SupplierSyncRun.Status.FAILED)
        self.assertIsNotNone(stalled.finished_at)
        self.assertEqual(run.status, SupplierSyncRun.Status.SUCCEEDED)

    def test_recent_run_prevents_overlap_before_adapter_is_called(self):
        SupplierSyncRun.objects.create(
            supplier_code="aliexpress", requested_by=self.requester
        )
        adapter = FixtureSupplierAdapter((self.make_product(),))

        with self.assertRaises(SupplierSyncAlreadyRunning):
            run_supplier_sync(adapter, requester=self.requester)

        self.assertFalse(adapter.iterated)
        self.assertEqual(SupplierSyncRun.objects.count(), 1)

    def test_import_requires_permission_before_calling_adapter(self):
        unprivileged = get_user_model().objects.create_user(
            "catalog-viewer@example.com", "Catalog-viewer-safe-passphrase-24!", is_staff=True
        )
        adapter = FixtureSupplierAdapter((self.make_product(),))

        with self.assertRaises(PermissionDenied):
            run_supplier_sync(adapter, requester=unprivileged)

        self.assertFalse(adapter.iterated)
        self.assertFalse(SupplierSyncRun.objects.exists())

    @override_settings(SUPPLIER_SYNC_ENABLED=False)
    def test_import_feature_flag_is_off_by_default_and_prevents_adapter_call(self):
        adapter = FixtureSupplierAdapter((self.make_product(),))

        with self.assertRaises(SupplierImportDisabled):
            run_supplier_sync(adapter, requester=self.requester)

        self.assertFalse(adapter.iterated)
        self.assertFalse(SupplierSyncRun.objects.exists())

    def test_snapshots_and_run_items_are_immutable(self):
        run = run_supplier_sync(
            FixtureSupplierAdapter((self.make_product(),)), requester=self.requester
        )
        snapshot = SupplierCatalogSnapshot.objects.get(sync_run=run)
        item = run.items.get()
        snapshot.name = "Changed"

        with self.assertRaises(ValidationError):
            snapshot.save()
        with self.assertRaises(ValidationError):
            SupplierCatalogSnapshot.objects.filter(pk=snapshot.pk).update(name="Changed")
        with self.assertRaises(ValidationError):
            item.delete()

    def test_sync_admin_is_read_only_and_permission_scoped(self):
        run = run_supplier_sync(
            FixtureSupplierAdapter((self.make_product(),)), requester=self.requester
        )
        view_permission = Permission.objects.get(
            content_type__app_label="catalog", codename="view_supplier_sync"
        )
        viewer = get_user_model().objects.create_user(
            "supplier-auditor@example.com", "Supplier-auditor-safe-passphrase-25!", is_staff=True
        )
        viewer.user_permissions.add(view_permission)
        self.client.force_login(viewer)

        run_only_user = get_user_model().objects.create_user(
            "supplier-operator-two@example.com",
            "Supplier-operator-two-safe-passphrase-26!",
            is_staff=True,
        )
        run_permission = Permission.objects.get(
            content_type__app_label="catalog", codename="run_supplier_sync"
        )
        run_only_user.user_permissions.add(run_permission)
        self.client.force_login(run_only_user)
        response = self.client.get(reverse("admin:catalog_suppliersyncrun_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].result_list), [])
        self.assertRedirects(
            self.client.get(
                reverse("admin:catalog_suppliersyncrun_change", args=(run.pk,))
            ),
            reverse("admin:index"),
        )

        self.client.force_login(viewer)
        response = self.client.get(reverse("admin:catalog_suppliersyncrun_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, run.supplier_code)
        detail_response = self.client.get(
            reverse("admin:catalog_suppliersyncrun_change", args=(run.pk,))
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, run.supplier_code)
        self.assertEqual(
            self.client.get(
                reverse("admin:catalog_suppliercatalogsnapshot_add")
            ).status_code,
            403,
        )
