from django.urls import path

from . import views

app_name = "storefront"

urlpatterns = [
    path("", views.home, name="home"),
    path("shop/", views.shop, name="shop"),
    path("products/<slug:slug>/", views.product_detail, name="product-detail"),
    path("account/", views.account, name="account"),
]
