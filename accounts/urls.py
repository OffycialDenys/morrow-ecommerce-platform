from django.urls import path

from .views import (
    EmailLoginView,
    EmailLogoutView,
    StorePasswordResetCompleteView,
    StorePasswordResetConfirmView,
    StorePasswordResetDoneView,
    StorePasswordResetView,
    register,
    saved_products,
    toggle_saved_product,
)

app_name = "accounts"

urlpatterns = [
    path("register/", register, name="register"),
    path("login/", EmailLoginView.as_view(), name="login"),
    path("logout/", EmailLogoutView.as_view(), name="logout"),
    path("password-reset/", StorePasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", StorePasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password-reset/<uidb64>/<token>/",
        StorePasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        StorePasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("saved/", saved_products, name="saved"),
    path("saved/<int:product_id>/toggle/", toggle_saved_product, name="toggle-saved"),
]
