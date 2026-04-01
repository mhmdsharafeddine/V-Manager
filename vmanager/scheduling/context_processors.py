from .notifications import build_user_notifications_context


def schedule_notifications(request):
    base = build_user_notifications_context(request.user)
    return {
        "nav_notifications": base["notifications"],
        "nav_notification_unread_count": base["notification_unread_count"],
    }
