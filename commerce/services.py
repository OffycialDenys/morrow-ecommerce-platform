import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from catalog.models import ProductVariant, validate_iso_currency

from .models import Order, OrderLine


class CartError(ValueError):
    """A customer action cannot be applied to the current cart."""


@dataclass(frozen=True)
class CartLine:
    variant_id: int
    quantity: int
    variant: ProductVariant | None
    available: bool
    unavailable_reason: str | None = None

    @property
    def line_total_minor(self) -> int | None:
        if not self.available or self.variant is None:
            return None
        return self.variant.price_minor * self.quantity


@dataclass(frozen=True)
class CartSummary:
    lines: list[CartLine]
    currency: str | None
    subtotal_minor: int | None
    can_continue: bool


def _settings():
    return {
        "max_quantity": getattr(settings, "CART_MAX_QUANTITY", 10),
    }


def _valid_session_items(session) -> dict[int, int]:
    stored = session.get("cart_items", {})
    if not isinstance(stored, dict):
        return {}
    result: dict[int, int] = {}
    maximum = _settings()["max_quantity"]
    for raw_id, raw_quantity in stored.items():
        try:
            variant_id = int(raw_id)
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            continue
        if variant_id > 0 and 1 <= quantity <= maximum:
            result[variant_id] = quantity
    return result


def _variant_is_available(variant: ProductVariant) -> bool:
    return variant.can_be_added_to_cart()


def _unavailable_reason(variant: ProductVariant) -> str:
    if not variant.product.is_published:
        return "This product is no longer available."
    if not variant.is_active:
        return "This option is no longer available."
    if variant.available_units is None:
        return "Availability has not been confirmed."
    if variant.available_units == 0:
        return "This option is out of stock."
    if variant.inventory_source == ProductVariant.InventorySource.SUPPLIER:
        return "Supplier availability needs to be checked again."
    return "This option is no longer available."


def _summary_from_variants(items: dict[int, int], found: dict[int, ProductVariant]) -> CartSummary:
    lines: list[CartLine] = []

    for variant_id, quantity in items.items():
        variant = found.get(variant_id)
        if variant is None:
            lines.append(CartLine(variant_id, quantity, None, False, "This item no longer exists."))
        elif not _variant_is_available(variant):
            lines.append(
                CartLine(variant_id, quantity, variant, False, _unavailable_reason(variant))
            )
        elif quantity > (variant.available_units or 0):
            lines.append(
                CartLine(
                    variant_id,
                    quantity,
                    variant,
                    False,
                    "The quantity in your bag is no longer available.",
                )
            )
        else:
            lines.append(CartLine(variant_id, quantity, variant, True))

    currencies = {line.variant.product.currency for line in lines if line.variant is not None}
    is_single_currency = len(currencies) == 1
    all_available = bool(lines) and all(line.available for line in lines)
    can_continue = all_available and is_single_currency
    currency = next(iter(currencies)) if is_single_currency else None
    subtotal = sum(line.line_total_minor or 0 for line in lines) if can_continue else None
    return CartSummary(lines, currency, subtotal, can_continue)


def cart_summary(session) -> CartSummary:
    items = _valid_session_items(session)
    variants = ProductVariant.objects.filter(pk__in=items).select_related("product")
    found = {variant.pk: variant for variant in variants}
    return _summary_from_variants(items, found)


def create_order_snapshot(
    user,
    session,
    *,
    idempotency_key,
    shipping_minor: int,
    tax_minor: int,
    currency: str,
) -> Order:
    """Persist a server-priced order snapshot from a trusted, server-calculated quote."""
    if not getattr(user, "is_authenticated", False) or user.pk is None:
        raise CartError("Sign in before placing an order.")
    if not isinstance(idempotency_key, UUID):
        raise CartError("A valid checkout idempotency key is required.")
    if type(shipping_minor) is not int or type(tax_minor) is not int:
        raise CartError("Shipping and tax must be integer minor-unit amounts.")
    if shipping_minor < 0 or tax_minor < 0:
        raise CartError("Shipping and tax amounts cannot be negative.")
    try:
        validate_iso_currency(currency)
    except ValidationError as error:
        raise CartError("Choose a supported currency for this order.") from error
    currency = currency.upper()

    max_amount = 2**63 - 1
    if shipping_minor > max_amount or tax_minor > max_amount:
        raise CartError("The quoted amount is outside the supported range.")

    items = _valid_session_items(session)
    fingerprint_payload = {
        "currency": currency,
        "items": sorted(items.items()),
        "shipping_minor": shipping_minor,
        "tax_minor": tax_minor,
        "user_id": user.pk,
    }
    request_fingerprint = sha256(
        json.dumps(fingerprint_payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()

    with transaction.atomic():
        existing = Order.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            if existing.user_id != user.pk:
                raise CartError("That checkout request cannot be used for this account.")
            if existing.request_fingerprint != request_fingerprint:
                raise CartError(
                    "That checkout request key was already used with different details."
                )
            return existing

        if not items:
            raise CartError("Your bag is empty.")
        variants = list(
            ProductVariant.objects.select_for_update()
            .filter(pk__in=items)
            .select_related("product")
            .order_by("pk")
        )
        found = {variant.pk: variant for variant in variants}
        summary = _summary_from_variants(items, found)
        if not summary.can_continue or summary.subtotal_minor is None:
            raise CartError("Your bag has unavailable items. Review it before continuing.")
        if summary.currency != currency:
            raise CartError("The shipping and tax quote does not match your bag's currency.")

        total_minor = summary.subtotal_minor + shipping_minor + tax_minor
        if total_minor > max_amount:
            raise CartError("The quoted order total is outside the supported range.")

        try:
            with transaction.atomic():
                order = Order.objects.create(
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    user=user,
                    buyer_email=user.email,
                    currency=currency,
                    subtotal_minor=summary.subtotal_minor,
                    shipping_minor=shipping_minor,
                    tax_minor=tax_minor,
                    total_minor=total_minor,
                )
                OrderLine.objects.bulk_create(
                    [
                        OrderLine(
                            order=order,
                            variant=found[line.variant_id],
                            product_name=found[line.variant_id].product.name,
                            variant_label=found[line.variant_id].label,
                            sku=found[line.variant_id].sku,
                            attributes=found[line.variant_id].attributes,
                            quantity=line.quantity,
                            unit_price_minor=found[line.variant_id].price_minor,
                            line_total_minor=found[line.variant_id].price_minor * line.quantity,
                        )
                        for line in summary.lines
                    ]
                )
        except IntegrityError:
            existing = Order.objects.filter(idempotency_key=idempotency_key).first()
            if existing is None:
                raise
            if existing.user_id != user.pk or existing.request_fingerprint != request_fingerprint:
                raise CartError(
                    "That checkout request key was already used with different details."
                )
            return existing
        return order


def add_variant(session, variant_id: int, quantity: int) -> None:
    if quantity < 1:
        raise CartError("Choose a quantity of at least one.")
    limits = _settings()
    if quantity > limits["max_quantity"]:
        raise CartError(f"You can add up to {limits['max_quantity']} of one option.")

    variant = ProductVariant.objects.select_related("product").filter(pk=variant_id).first()
    if variant is None or not _variant_is_available(variant):
        raise CartError("That option is unavailable. Please refresh the product page.")

    items = _valid_session_items(session)
    other_ids = [item_id for item_id in items if item_id != variant_id]
    other_variants = ProductVariant.objects.filter(pk__in=other_ids).select_related("product")
    currencies = {item.product.currency for item in other_variants}
    if currencies and currencies != {variant.product.currency}:
        raise CartError("A cart can contain products in one currency at a time.")

    new_quantity = items.get(variant_id, 0) + quantity
    if new_quantity > limits["max_quantity"] or new_quantity > (variant.available_units or 0):
        raise CartError("That quantity is greater than the currently available amount.")
    items[variant_id] = new_quantity
    session["cart_items"] = {str(key): value for key, value in items.items()}
    session.modified = True


def set_variant_quantity(session, variant_id: int, quantity: int) -> None:
    if quantity < 1:
        raise CartError("Choose a quantity of at least one.")
    limits = _settings()
    items = _valid_session_items(session)
    if variant_id not in items:
        raise CartError("That item is no longer in your cart.")
    if quantity > limits["max_quantity"]:
        raise CartError(f"You can order up to {limits['max_quantity']} of one option.")
    variant = ProductVariant.objects.select_related("product").filter(pk=variant_id).first()
    if variant is None or not _variant_is_available(variant):
        raise CartError("This item is unavailable. Remove it from your cart to continue.")
    if quantity > (variant.available_units or 0):
        raise CartError("That quantity is greater than the currently available amount.")
    items[variant_id] = quantity
    session["cart_items"] = {str(key): value for key, value in items.items()}
    session.modified = True


def remove_variant(session, variant_id: int) -> None:
    items = _valid_session_items(session)
    items.pop(variant_id, None)
    session["cart_items"] = {str(key): value for key, value in items.items()}
    session.modified = True


def count_cart_items(session) -> int:
    return sum(_valid_session_items(session).values())
