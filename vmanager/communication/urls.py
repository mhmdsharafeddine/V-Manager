from django.urls import path

from . import views

app_name = "communication"

urlpatterns = [
    path("", views.hub, name="hub"),
    path("announcements/", views.announcements_page, name="announcements"),
    path("announcements/poll/", views.api_announcements_poll, name="announcements_poll_api"),
    path("announcements/<int:announcement_id>/read/", views.mark_announcement_read, name="mark_read"),
    path("announcements/<int:announcement_id>/pin/", views.toggle_pin_announcement, name="toggle_pin"),
    path("announcements/<int:announcement_id>/comments/", views.api_announcement_comments, name="comments_api"),
    # Private Chat
    path("chat/", views.private_chat, name="chat"),
    path("chat/<int:peer_id>/messages/", views.api_chat_messages, name="chat_messages_api"),
    path("chat/<int:peer_id>/send/", views.api_chat_send, name="chat_send_api"),
    path("chat/unread/", views.api_chat_unread_counts, name="chat_unread_api"),
]
