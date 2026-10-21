from django.contrib import admin
from django.urls import include, path

from catalog import api as catalog_api
from sitecore import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/live/", core_views.live, name="health-live"),
    path("health/ready/", core_views.ready, name="health-ready"),
    path("api/v1/products/", catalog_api.product_list, name="api-product-list"),
    path("api/v1/products/<slug:slug>/", catalog_api.product_detail, name="api-product-detail"),
    path("", include("storefront.urls")),
    path("accounts/", include("accounts.urls")),
    path("cart/", include("commerce.urls")),
    path("reviews/", include("reviews.urls")),
    path("", include("promotions.urls")),
]
