from decimal import Decimal

from babel.numbers import format_currency, get_currency_precision
from django.conf import settings


def minor_to_major(amount_minor: int, currency: str) -> Decimal:
    """Convert integer ISO minor units to a Decimal without floating-point arithmetic."""
    precision = get_currency_precision(currency)
    return Decimal(amount_minor).scaleb(-precision)


def major_to_minor(amount_major: Decimal, currency: str) -> int:
    """Convert an exact major-unit decimal to minor units without rounding."""
    precision = get_currency_precision(currency)
    scaled = amount_major.scaleb(precision)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"{currency} supports {precision} fractional digits.")
    return int(integral)


def format_minor(amount_minor: int, currency: str) -> str:
    return format_currency(
        minor_to_major(amount_minor, currency),
        currency,
        "¤¤ #,##0.00",
        locale=getattr(settings, "CURRENCY_LOCALE", "en_US"),
        currency_digits=True,
    )
