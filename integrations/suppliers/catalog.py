"""Validated, bounded import boundary for provider-normalized catalog records."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import islice
from typing import Iterable, Mapping, Protocol

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from catalog.models import (
    SupplierCatalogSnapshot,
    SupplierSyncItem,
    SupplierSyncRun,
    https_url_validator,
    validate_iso_currency,
)

logger = logging.getLogger(__name__)
MAX_SUPPLIER_SYNC_RECORDS = 100
MAX_SUPPLIER_VARIANTS = 200
MAX_SUPPLIER_ATTRIBUTES = 20
MAX_SUPPLIER_PRICE_MINOR = 2**63 - 1
MAX_SUPPLIER_RECORD_BYTES = 64 * 1024
SUPPLIER_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


@dataclass(frozen=True)
class SupplierVariantData:
    supplier_variant_id: str
    supplier_price_minor: int
    currency: str
    sku: str = ""
    label: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SupplierProductData:
    supplier_product_id: str
    name: str
    variants: tuple[SupplierVariantData, ...]
    description: str = ""
    image_url: str = ""


class SupplierCatalogAdapter(Protocol):
    """Return provider data mapped to this contract, with bounded I/O and timeouts."""

    supplier_code: str

    def iter_products(self, *, limit: int) -> Iterable[SupplierProductData]: ...


class SupplierImportError(ValueError):
    """An import cannot be started or completed under the current rules."""


class SupplierImportDisabled(SupplierImportError):
    """Supplier imports are disabled by deployment configuration."""


class SupplierSyncAlreadyRunning(SupplierImportError):
    """A non-stale sync run already holds this supplier's import slot."""


class SupplierAdapterError(SupplierImportError):
    """A provider adapter failed with a safe, operator-facing error."""

    SAFE_MESSAGES = {
        "configuration_missing": "The supplier adapter is not configured.",
        "invalid_response": "The supplier service returned an invalid response.",
        "permission_denied": "The supplier account is not authorized for this request.",
        "provider_error": "The supplier request failed.",
        "provider_unavailable": "The supplier service is temporarily unavailable.",
        "rate_limited": "The supplier service temporarily limited requests.",
    }

    def __init__(self, code: str):
        if code not in self.SAFE_MESSAGES:
            raise ValueError("Supplier adapter errors must use a known safe error code.")
        self.code = code
        self.public_message = self.SAFE_MESSAGES[code]
        super().__init__(self.public_message)


class SupplierRecordError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.public_message = message
        super().__init__(message)


def _clean_text(value, *, field_name: str, max_length: int, optional: bool = False) -> str:
    if not isinstance(value, str):
        raise SupplierRecordError("invalid_text", f"{field_name} must be text.")
    cleaned = value.strip()
    if (not cleaned and not optional) or len(cleaned) > max_length or "\x00" in cleaned:
        raise SupplierRecordError("invalid_text", f"{field_name} is empty or exceeds its limit.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in cleaned):
        raise SupplierRecordError("invalid_text", f"{field_name} contains unsupported characters.")
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SupplierRecordError(
            "invalid_text", f"{field_name} contains unsupported characters."
        ) from error
    return cleaned


def _normalise_product(product: SupplierProductData) -> dict:
    if not isinstance(product, SupplierProductData):
        raise SupplierRecordError("invalid_record", "The adapter returned an unsupported record.")

    product_id = _clean_text(
        product.supplier_product_id, field_name="Supplier product ID", max_length=120
    )
    name = _clean_text(product.name, field_name="Product name", max_length=160)
    description = _clean_text(
        product.description, field_name="Description", max_length=10_000, optional=True
    )
    image_url = _clean_text(
        product.image_url, field_name="Image URL", max_length=500, optional=True
    )
    if image_url:
        try:
            https_url_validator(image_url)
        except ValidationError as error:
            raise SupplierRecordError(
                "invalid_image_url", "The supplier image URL must use HTTPS."
            ) from error

    if not isinstance(product.variants, (tuple, list)) or not 1 <= len(product.variants) <= MAX_SUPPLIER_VARIANTS:
        raise SupplierRecordError(
            "invalid_variants", f"A product must contain 1 to {MAX_SUPPLIER_VARIANTS} variants."
        )

    variants = []
    seen_variant_ids = set()
    for variant in product.variants:
        if not isinstance(variant, SupplierVariantData):
            raise SupplierRecordError("invalid_variant", "A supplier variant is malformed.")
        variant_id = _clean_text(
            variant.supplier_variant_id, field_name="Supplier variant ID", max_length=120
        )
        if variant_id in seen_variant_ids:
            raise SupplierRecordError("duplicate_variant", "Supplier variant IDs must be unique.")
        seen_variant_ids.add(variant_id)

        sku = _clean_text(variant.sku, field_name="Supplier SKU", max_length=80, optional=True)
        label = _clean_text(
            variant.label, field_name="Variant label", max_length=120, optional=True
        )
        if (
            type(variant.supplier_price_minor) is not int  # noqa: E721
            or not 1 <= variant.supplier_price_minor <= MAX_SUPPLIER_PRICE_MINOR
        ):
            raise SupplierRecordError(
                "invalid_supplier_price", "Supplier prices must be positive integer minor units."
            )
        currency = _clean_text(variant.currency, field_name="Currency", max_length=3).upper()
        try:
            validate_iso_currency(currency)
        except ValidationError as error:
            raise SupplierRecordError("invalid_currency", "The supplier currency is invalid.") from error

        if not isinstance(variant.attributes, Mapping) or len(variant.attributes) > MAX_SUPPLIER_ATTRIBUTES:
            raise SupplierRecordError("invalid_attributes", "Variant attributes are malformed.")
        attributes = {}
        for key, value in variant.attributes.items():
            attribute_name = _clean_text(key, field_name="Attribute name", max_length=80)
            attribute_value = _clean_text(
                value, field_name="Attribute value", max_length=240, optional=True
            )
            if attribute_name in attributes:
                raise SupplierRecordError(
                    "duplicate_attribute", "Variant attribute names must be unique."
                )
            attributes[attribute_name] = attribute_value

        variants.append(
            {
                "supplier_variant_id": variant_id,
                "sku": sku,
                "label": label,
                "attributes": dict(sorted(attributes.items())),
                "supplier_price_minor": variant.supplier_price_minor,
                "currency": currency,
            }
        )

    return {
        "supplier_product_id": product_id,
        "name": name,
        "description": description,
        "image_url": image_url,
        "variants": variants,
    }


def _digest_product(product: dict) -> str:
    serialized = json.dumps(product, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    encoded = serialized.encode("utf-8")
    if len(encoded) > MAX_SUPPLIER_RECORD_BYTES:
        raise SupplierRecordError(
            "record_too_large", "The supplier product record exceeds the storage limit."
        )
    return hashlib.sha256(encoded).hexdigest()


def _candidate_product_id(product) -> str:
    if not isinstance(product, SupplierProductData):
        return ""
    try:
        return _clean_text(
            product.supplier_product_id,
            field_name="Supplier product ID",
            max_length=120,
        )
    except SupplierRecordError:
        return ""


def _record_item(
    run: SupplierSyncRun,
    *,
    record_number: int,
    outcome: str,
    supplier_product_id: str = "",
    snapshot: SupplierCatalogSnapshot | None = None,
    error_code: str = "",
    error_message: str = "",
) -> None:
    counter_by_outcome = {
        SupplierSyncItem.Outcome.STAGED: "staged_count",
        SupplierSyncItem.Outcome.VALIDATED: "validated_count",
        SupplierSyncItem.Outcome.DUPLICATE: "skipped_count",
        SupplierSyncItem.Outcome.FAILED: "failed_count",
    }
    counter = counter_by_outcome[outcome]
    now = timezone.now()
    with transaction.atomic():
        SupplierSyncItem.objects.create(
            sync_run=run,
            record_number=record_number,
            supplier_product_id=supplier_product_id,
            outcome=outcome,
            error_code=error_code,
            error_message=error_message,
            snapshot=snapshot,
        )
        SupplierSyncRun.objects.filter(pk=run.pk).update(
            records_seen=F("records_seen") + 1,
            **{counter: F(counter) + 1},
            last_progress_at=now,
        )


def _begin_run(supplier_code: str, requester, dry_run: bool) -> SupplierSyncRun:
    now = timezone.now()
    stale_after = timedelta(
        seconds=settings.SUPPLIER_SYNC_STALE_AFTER_SECONDS
    )
    try:
        with transaction.atomic():
            current = (
                SupplierSyncRun.objects.select_for_update()
                .filter(supplier_code=supplier_code, status=SupplierSyncRun.Status.RUNNING)
                .first()
            )
            if current is not None:
                if current.last_progress_at > now - stale_after:
                    raise SupplierSyncAlreadyRunning(
                        "A supplier import is already running for this provider."
                    )
                current.status = SupplierSyncRun.Status.FAILED
                current.finished_at = now
                current.error_summary = "The previous import stopped making progress."
                current.save(
                    update_fields=("status", "finished_at", "error_summary")
                )
            return SupplierSyncRun.objects.create(
                supplier_code=supplier_code,
                requested_by=requester,
                dry_run=dry_run,
            )
    except IntegrityError as error:
        raise SupplierSyncAlreadyRunning(
            "A supplier import is already running for this provider."
        ) from error


def _finish_run(run: SupplierSyncRun, *, error_summary: str = "") -> SupplierSyncRun:
    run.refresh_from_db()
    now = timezone.now()
    if error_summary:
        status = (
            SupplierSyncRun.Status.PARTIAL
            if run.records_seen
            else SupplierSyncRun.Status.FAILED
        )
    elif run.failed_count:
        status = SupplierSyncRun.Status.PARTIAL
    else:
        status = SupplierSyncRun.Status.SUCCEEDED
    run.status = status
    run.error_summary = error_summary
    run.finished_at = now
    run.last_progress_at = now
    run.save(
        update_fields=("status", "error_summary", "finished_at", "last_progress_at")
    )
    return run


def run_supplier_sync(
    adapter: SupplierCatalogAdapter,
    *,
    requester,
    dry_run: bool = False,
    limit: int = MAX_SUPPLIER_SYNC_RECORDS,
) -> SupplierSyncRun:
    """Stage a bounded normalized supplier batch without modifying the live catalog."""
    if not settings.SUPPLIER_SYNC_ENABLED:
        raise SupplierImportDisabled("Supplier catalog imports are disabled.")
    if requester is None or not requester.has_perm("catalog.run_supplier_sync"):
        raise PermissionDenied("You do not have permission to run supplier imports.")
    if type(dry_run) is not bool:  # noqa: E721
        raise SupplierImportError("The dry-run option must be a boolean.")
    if type(limit) is not int or not 1 <= limit <= MAX_SUPPLIER_SYNC_RECORDS:  # noqa: E721
        raise SupplierImportError(
            f"Supplier imports are limited to 1 to {MAX_SUPPLIER_SYNC_RECORDS} records."
        )

    supplier_code = getattr(adapter, "supplier_code", None)
    if not isinstance(supplier_code, str) or not SUPPLIER_CODE_PATTERN.fullmatch(supplier_code):
        raise SupplierImportError("The supplier adapter has an invalid provider code.")
    iter_products = getattr(adapter, "iter_products", None)
    if not callable(iter_products):
        raise SupplierImportError("The supplier adapter does not implement iter_products().")

    run = _begin_run(supplier_code, requester, dry_run)
    seen_digests = {}
    error_summary = ""
    unexpected_error = None
    try:
        products = iter_products(limit=limit)
        for record_number, product in enumerate(islice(iter(products), limit + 1), start=1):
            if record_number > limit:
                error_summary = "The supplier adapter exceeded the requested batch limit."
                break
            product_id = _candidate_product_id(product)
            try:
                normalized = _normalise_product(product)
                product_id = normalized["supplier_product_id"]
                digest = _digest_product(normalized)
                previous_digest = seen_digests.get(product_id)
                if previous_digest is not None:
                    if previous_digest == digest:
                        _record_item(
                            run,
                            record_number=record_number,
                            outcome=SupplierSyncItem.Outcome.DUPLICATE,
                            supplier_product_id=product_id,
                        )
                    else:
                        _record_item(
                            run,
                            record_number=record_number,
                            outcome=SupplierSyncItem.Outcome.FAILED,
                            supplier_product_id=product_id,
                            error_code="duplicate_conflict",
                            error_message=(
                                "The provider returned conflicting data for this product in one batch."
                            ),
                        )
                    continue

                seen_digests[product_id] = digest
                snapshot = None
                outcome = SupplierSyncItem.Outcome.VALIDATED
                if not dry_run:
                    with transaction.atomic():
                        snapshot = SupplierCatalogSnapshot.objects.create(
                            sync_run=run,
                            supplier_code=supplier_code,
                            supplier_product_id=product_id,
                            name=normalized["name"],
                            description=normalized["description"],
                            image_url=normalized["image_url"],
                            variants=normalized["variants"],
                            source_digest=digest,
                        )
                        outcome = SupplierSyncItem.Outcome.STAGED
                        _record_item(
                            run,
                            record_number=record_number,
                            outcome=outcome,
                            supplier_product_id=product_id,
                            snapshot=snapshot,
                        )
                else:
                    _record_item(
                        run,
                        record_number=record_number,
                        outcome=outcome,
                        supplier_product_id=product_id,
                    )
            except SupplierRecordError as error:
                _record_item(
                    run,
                    record_number=record_number,
                    outcome=SupplierSyncItem.Outcome.FAILED,
                    supplier_product_id=product_id,
                    error_code=error.code,
                    error_message=error.public_message,
                )
    except SupplierAdapterError as error:
        error_summary = error.public_message
    except Exception as error:
        logger.error(
            "Unexpected supplier adapter failure in run %s (%s)",
            run.pk,
            type(error).__name__,
        )
        error_summary = "The supplier import stopped because of an internal error."
        unexpected_error = error

    run = _finish_run(run, error_summary=error_summary)
    if unexpected_error is not None:
        raise unexpected_error
    return run
