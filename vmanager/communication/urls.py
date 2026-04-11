from django.urls import path

from . import views

app_name = "communication"

urlpatterns = [
    path("", views.hub, name="hub"),
    path("announcements/", views.announcements_page, name="announcements"),
    path("announcements/<int:announcement_id>/read/", views.mark_announcement_read, name="mark_read"),
]
