from django.urls import path

from . import views

app_name = "commerce"

urlpatterns = [
    path("", views.cart, name="cart"),
    path("add/", views.add, name="add"),
    path("update/<int:variant_id>/", views.update, name="update"),
    path("remove/<int:variant_id>/", views.remove, name="remove"),
    path("checkout/", views.checkout, name="checkout"),
]
