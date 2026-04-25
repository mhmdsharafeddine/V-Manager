from django.urls import path

from . import views


app_name = "performance"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("summary/", views.dashboard_summary_api, name="dashboard_summary"),
    path("records/new/", views.create_record_view, name="record_create"),
    path("records/<int:record_id>/edit/", views.edit_record_view, name="record_edit"),
]
