from django.conf import settings

from commerce.services import count_cart_items


def storefront_context(request):
    return {
        "cart_item_count": count_cart_items(request.session),
        "giveaways_enabled": settings.GIVEAWAYS_ENABLED,
    }
