from dataclasses import dataclass

from django.conf import settings

from catalog.models import ProductVariant


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


def cart_summary(session) -> CartSummary:
    items = _valid_session_items(session)
    variants = ProductVariant.objects.filter(pk__in=items).select_related("product")
    found = {variant.pk: variant for variant in variants}
    lines: list[CartLine] = []

    for variant_id, quantity in items.items():
        variant = found.get(variant_id)
        if variant is None:
            lines.append(CartLine(variant_id, quantity, None, False, "This item no longer exists."))
        elif _variant_is_available(variant):
            lines.append(CartLine(variant_id, quantity, variant, True))
        else:
            lines.append(
                CartLine(variant_id, quantity, variant, False, _unavailable_reason(variant))
            )

    currencies = {line.variant.product.currency for line in lines if line.variant is not None}
    is_single_currency = len(currencies) == 1
    all_available = bool(lines) and all(line.available for line in lines)
    can_continue = all_available and is_single_currency
    currency = next(iter(currencies)) if is_single_currency else None
    subtotal = sum(line.line_total_minor or 0 for line in lines) if can_continue else None
    return CartSummary(lines, currency, subtotal, can_continue)


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
