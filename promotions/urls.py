from django.urls import path

from . import views

app_name = "promotions"

urlpatterns = [
    path("giveaways/", views.giveaway_list, name="giveaway-list"),
    path("giveaways/<slug:slug>/", views.giveaway_detail, name="giveaway-detail"),
    path("giveaways/<slug:slug>/enter/", views.enter_free_giveaway, name="giveaway-enter"),
]
