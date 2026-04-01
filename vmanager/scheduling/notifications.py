from django.utils import timezone
from django.urls import reverse

from accounts.models import AccountProfile

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
        "event_id": event.id,
        "title": title,
        "event_title": event.title,
        "when": local_dt.strftime("%a, %d %b at %I:%M %p"),
        "class_name": class_name,
        "is_unread": event.id not in read_event_ids,
        "source_url": f"{reverse('scheduling:home')}?ym={ym}#event-{event.id}",
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
    if limit is not None:
        visible_events = visible_events[:limit]

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

    return [_build_notification_item(event, read_event_ids, now) for event in filtered_events]


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


def delete_notifications(user, event_ids):
    if not getattr(user, "is_authenticated", False):
        return

    for event_id in event_ids:
        EventNotificationRead.objects.update_or_create(
            user=user,
            event_id=event_id,
            defaults={"is_deleted": True},
        )
