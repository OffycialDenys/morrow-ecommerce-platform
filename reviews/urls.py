from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("order-line/<int:order_line_id>/", views.submit, name="submit"),
    path("<int:review_id>/report/", views.report, name="report"),
    path("<int:review_id>/appeal/", views.appeal, name="appeal"),
]
