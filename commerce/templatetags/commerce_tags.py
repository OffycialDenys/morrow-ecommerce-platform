from django import template

from commerce.money import format_minor

register = template.Library()


@register.filter
def money(amount_minor: int, currency: str) -> str:
    return format_minor(amount_minor, currency)
