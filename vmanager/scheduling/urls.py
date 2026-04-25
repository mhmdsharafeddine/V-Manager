from django.urls import path

from . import views


app_name = "scheduling"

urlpatterns = [
    path("", views.calendar_view, name="home"),
    path("attendance/", views.attendance_dashboard_view, name="attendance_dashboard"),
    path("api/attendance/stats/", views.manager_attendance_stats_view, name="attendance_stats_api"),
    path("api/coach/upcoming-events/", views.coach_upcoming_events_view, name="coach_upcoming_events_api"),
    path("notifications/", views.notifications_view, name="notifications"),
    path("notifications/<int:event_id>/read/", views.mark_notification_read_view, name="mark_notification_read"),
    path("notifications/<int:event_id>/open/", views.open_notification_event_view, name="open_notification_event"),
    path("notifications/<int:event_id>/delete/", views.delete_notification_view, name="delete_notification"),
    path(
        "notifications/announcements/<int:announcement_id>/delete/",
        views.delete_announcement_notification_view,
        name="delete_announcement_notification",
    ),
    path("notifications/read-all/", views.mark_all_notifications_read_view, name="mark_all_notifications_read"),
    path("events/create/", views.event_create_view, name="event_create"),
    path("events/<int:event_id>/", views.event_detail_view, name="event_detail"),
    path("events/<int:event_id>/attendance/", views.player_event_rsvp_view, name="event_rsvp"),
    path("events/<int:event_id>/edit/", views.edit_event_view, name="edit_event"),
    path("events/<int:event_id>/delete/", views.delete_event_view, name="delete_event"),
    path("calendar/", views.calendar_view, name="calendar"),
]
