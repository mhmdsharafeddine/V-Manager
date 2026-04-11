from django.utils import timezone
from django.urls import reverse

from accounts.models import AccountProfile
from communication.models import Announcement, AnnouncementRecipient

from .models import EventNotificationRead, ScheduledEvent


def _parse_targets(value):
    tokens = [item.strip().lower() for item in (value or "all").split(",") if item.strip()]
    if not tokens:
        return [ScheduledEvent.AUDIENCE_ALL]
    if ScheduledEvent.AUDIENCE_PRIVATE in tokens:
        return [ScheduledEvent.AUDIENCE_PRIVATE]
    if ScheduledEvent.AUDIENCE_ALL in tokens:
        return [ScheduledEvent.AUDIENCE_ALL]
    return list(dict.fromkeys(tokens))


def _viewer_target(user):
    role = getattr(getattr(user, "profile", None), "role", None)
    if role == AccountProfile.ROLE_PLAYER:
        return ScheduledEvent.AUDIENCE_PLAYERS
    if role == AccountProfile.ROLE_PARENT:
        return ScheduledEvent.AUDIENCE_PARENTS
    if role == AccountProfile.ROLE_COACH:
        return ScheduledEvent.AUDIENCE_COACHES
    if role == AccountProfile.ROLE_STAFF:
        return ScheduledEvent.AUDIENCE_STAFF
    if role == AccountProfile.ROLE_MANAGER:
        return ScheduledEvent.AUDIENCE_MANAGERS
    return None


def _get_user_team(user):
    """Get the team for the user"""
    membership = getattr(user, "team_membership", None)
    if membership:
        return membership.team
    return None


def _is_visible(event, user):
    # Check if user is on the same team as the event
    user_team = _get_user_team(user)
    if event.team is not None and (user_team is None or event.team_id != user_team.id):
        return False
    
    if event.created_by_id == user.id:
        return True

    targets = _parse_targets(event.audience)
    if ScheduledEvent.AUDIENCE_PRIVATE in targets:
        return False
    if ScheduledEvent.AUDIENCE_ALL in targets:
        return True

    role = getattr(getattr(user, "profile", None), "role", None)
    if role == AccountProfile.ROLE_MANAGER:
        return True
    target = _viewer_target(user)
    return target in targets if target else False


def _build_notification_item(event, read_event_ids, now):
    total_minutes = int((event.scheduled_at - now).total_seconds() // 60)
    local_dt = timezone.localtime(event.scheduled_at)
    ym = local_dt.strftime("%Y-%m")

    if total_minutes <= 120:
        title = "Starting Soon"
        class_name = "notify-urgent"
    elif total_minutes <= 24 * 60:
        title = "Upcoming Today"
        class_name = "notify-soon"
    else:
        title = "Upcoming Event"
        class_name = "notify-normal"

    return {
        "kind": "event",
        "event_id": event.id,
        "announcement_id": None,
        "title": title,
        "event_title": event.title,
        "when": local_dt.strftime("%a, %d %b at %I:%M %p"),
        "class_name": class_name,
        "is_unread": event.id not in read_event_ids,
        "source_url": f"{reverse('scheduling:home')}?ym={ym}#event-{event.id}",
        "open_action_url": reverse("scheduling:open_notification_event", args=[event.id]),
        "open_next_url": f"{reverse('scheduling:home')}?ym={ym}#event-{event.id}",
        "open_label": "Open Event",
        "mark_read_action_url": reverse("scheduling:mark_notification_read", args=[event.id]),
        "mark_read_next_url": reverse("scheduling:notifications"),
        "delete_action_url": reverse("scheduling:delete_notification", args=[event.id]),
        "delete_next_url": reverse("scheduling:notifications"),
    }


def _announcement_class_name(priority):
    if priority == Announcement.PRIORITY_URGENT:
        return "notify-urgent"
    if priority == Announcement.PRIORITY_IMPORTANT:
        return "notify-soon"
    return "notify-normal"


def _announcement_title(priority):
    if priority == Announcement.PRIORITY_URGENT:
        return "Urgent Announcement"
    if priority == Announcement.PRIORITY_IMPORTANT:
        return "Important Announcement"
    return "New Announcement"


def _build_announcement_notification_item(recipient):
    announcement = recipient.announcement
    created_local = timezone.localtime(announcement.created_at)

    return {
        "kind": "announcement",
        "event_id": None,
        "announcement_id": announcement.id,
        "title": _announcement_title(announcement.priority),
        "event_title": announcement.title,
        "when": created_local.strftime("%a, %d %b at %I:%M %p"),
        "class_name": _announcement_class_name(announcement.priority),
        "is_unread": recipient.read_at is None,
        "source_url": reverse("communication:announcements"),
        "open_action_url": reverse("communication:mark_read", args=[announcement.id]),
        "open_next_url": reverse("communication:announcements"),
        "open_label": "Open Announcement",
        "mark_read_action_url": reverse("communication:mark_read", args=[announcement.id]),
        "mark_read_next_url": reverse("scheduling:notifications"),
        "delete_action_url": None,
        "delete_next_url": None,
    }


def get_user_notifications(user, *, limit=None):
    if not getattr(user, "is_authenticated", False):
        return []

    now = timezone.now()
    events = (
        ScheduledEvent.objects.filter(status=ScheduledEvent.STATUS_SCHEDULED, scheduled_at__gte=now)
        .order_by("scheduled_at")
    )
    visible_events = [event for event in events if _is_visible(event, user)]

    event_ids = [event.id for event in visible_events]
    state_map = {
        row["event_id"]: row["is_deleted"]
        for row in EventNotificationRead.objects.filter(user=user, event_id__in=event_ids).values("event_id", "is_deleted")
    }
    filtered_events = [event for event in visible_events if not state_map.get(event.id, False)]
    read_event_ids = set(
        EventNotificationRead.objects.filter(user=user, event_id__in=[event.id for event in filtered_events]).values_list(
            "event_id", flat=True
        )
    )

    event_notifications = [_build_notification_item(event, read_event_ids, now) for event in filtered_events]

    announcement_rows = (
        AnnouncementRecipient.objects.filter(
            user=user,
            announcement__send_push_notification=True,
        )
        .select_related("announcement")
        .order_by("-announcement__created_at", "-announcement_id")
    )
    announcement_notifications = [_build_announcement_notification_item(row) for row in announcement_rows]

    notifications = announcement_notifications + event_notifications
    if limit is not None:
        notifications = notifications[:limit]

    return notifications


def build_user_notifications_context(user, *, limit=8):
    notifications = get_user_notifications(user, limit=limit)
    unread_count = sum(1 for item in notifications if item["is_unread"])
    return {"notifications": notifications, "notification_unread_count": unread_count}


def mark_notifications_read(user, event_ids):
    if not getattr(user, "is_authenticated", False):
        return

    for event_id in event_ids:
        EventNotificationRead.objects.update_or_create(
            user=user,
            event_id=event_id,
            defaults={"is_deleted": False},
        )


def mark_announcement_notifications_read(user, announcement_ids):
    if not getattr(user, "is_authenticated", False):
        return
    if not announcement_ids:
        return

    AnnouncementRecipient.objects.filter(
        user=user,
        announcement_id__in=announcement_ids,
        announcement__send_push_notification=True,
        read_at__isnull=True,
    ).update(read_at=timezone.now())


def mark_all_user_notifications_read(user):
    if not getattr(user, "is_authenticated", False):
        return 0

    notifications = get_user_notifications(user)
    event_ids = [item["event_id"] for item in notifications if item.get("event_id") and item["is_unread"]]
    announcement_ids = [
        item["announcement_id"]
        for item in notifications
        if item.get("announcement_id") and item["is_unread"]
    ]

    if event_ids:
        mark_notifications_read(user, event_ids)
    if announcement_ids:
        mark_announcement_notifications_read(user, announcement_ids)

    return len(event_ids) + len(announcement_ids)


def delete_notifications(user, event_ids):
    if not getattr(user, "is_authenticated", False):
        return

    for event_id in event_ids:
        EventNotificationRead.objects.update_or_create(
            user=user,
            event_id=event_id,
            defaults={"is_deleted": True},
        )
