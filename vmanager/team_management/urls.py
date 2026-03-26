from django.urls import path

from . import views


app_name = "team_management"

urlpatterns = [
    path("roster/", views.roster_view, name="roster"),
    path("members/add/", views.add_member_view, name="add_member"),
    path("members/<int:membership_id>/edit/", views.edit_member_view, name="edit_member"),
]
