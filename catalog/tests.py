from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product, ProductVariant


class CatalogViewsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Home", slug="home")
        self.product = Product.objects.create(
            name="Stoneware Cup",
            slug="stoneware-cup",
            category=self.category,
            description="A useful cup.",
            currency="USD",
            publication_status=Product.PublicationStatus.PUBLISHED,
        )
        ProductVariant.objects.create(
            product=self.product,
            sku="CUP-OAT",
            label="Oat",
            attributes={"color": "Oat"},
            price_minor=1899,
            available_units=4,
        )
        self.draft = Product.objects.create(
            name="Unlisted Tray",
            slug="unlisted-tray",
            currency="USD",
            publication_status=Product.PublicationStatus.DRAFT,
        )
        ProductVariant.objects.create(
            product=self.draft, sku="TRAY", price_minor=2500, available_units=3
        )

    def test_shop_lists_only_published_products(self):
        response = self.client.get(reverse("storefront:shop"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stoneware Cup")
        self.assertNotContains(response, "Unlisted Tray")

    def test_unpublished_product_detail_is_not_public(self):
        response = self.client.get(reverse("storefront:product-detail", args=[self.draft.slug]))

        self.assertEqual(response.status_code, 404)

    def test_product_description_is_autoescaped(self):
        self.product.description = "<script>alert('x')</script>"
        self.product.save()

        response = self.client.get(reverse("storefront:product-detail", args=[self.product.slug]))

        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert")

    def test_price_filter_uses_currency_minor_units(self):
        response = self.client.get(
            reverse("api-product-list"),
            {"currency": "USD", "minPrice": "18.00", "maxPrice": "19.00"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["slug"] for item in response.json()["results"]], [self.product.slug])

    def test_price_filter_requires_currency_and_valid_currency_precision(self):
        missing_currency = self.client.get(reverse("api-product-list"), {"minPrice": "10.00"})
        too_precise = self.client.get(
            reverse("api-product-list"),
            {"currency": "USD", "minPrice": "10.001"},
        )

        self.assertEqual(missing_currency.status_code, 400)
        self.assertEqual(missing_currency.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(too_precise.status_code, 400)

    def test_price_sort_requires_currency_and_sort_field_is_allowlisted(self):
        no_currency = self.client.get(reverse("api-product-list"), {"sort": "price_asc"})
        injection = self.client.get(
            reverse("api-product-list"), {"sort": "name; DROP TABLE catalog_product"}
        )

        self.assertEqual(no_currency.status_code, 400)
        self.assertEqual(injection.status_code, 400)
        self.assertEqual(Product.objects.count(), 2)

    def test_json_api_hides_draft_products_and_returns_variant_details(self):
        list_response = self.client.get(reverse("api-product-list"))
        detail_response = self.client.get(reverse("api-product-detail", args=[self.product.slug]))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 1)
        self.assertEqual(list_response.json()["results"][0]["currency"], "USD")
        self.assertEqual(detail_response.json()["variants"][0]["attributes"], {"color": "Oat"})

    def test_product_api_rejects_invalid_page(self):
        response = self.client.get(reverse("api-product-list"), {"page": "not-a-number"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
