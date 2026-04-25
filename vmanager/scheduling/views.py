import calendar
import json
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import AccountProfile
from team_management.models import TeamMembership

from .models import EventAttendance, ScheduledEvent
from .notifications import (
    build_user_notifications_context,
    delete_announcement_notifications,
    delete_notifications,
    get_user_notifications,
    mark_all_user_notifications_read,
    mark_notifications_read,
)


EVENT_TYPE_MAP = {
    "practice": ScheduledEvent.TYPE_PRACTICE,
    "match": ScheduledEvent.TYPE_MATCH,
    "tournament": ScheduledEvent.TYPE_TOURNAMENT,
    "training": ScheduledEvent.TYPE_TRAINING,
    "other": ScheduledEvent.TYPE_OTHER,
}

AUDIENCE_MAP = {
    "all": ScheduledEvent.AUDIENCE_ALL,
    "players": ScheduledEvent.AUDIENCE_PLAYERS,
    "coaches": ScheduledEvent.AUDIENCE_COACHES,
    "staff": ScheduledEvent.AUDIENCE_STAFF,
    "parents": ScheduledEvent.AUDIENCE_PARENTS,
    "managers": ScheduledEvent.AUDIENCE_MANAGERS,
    "private": ScheduledEvent.AUDIENCE_PRIVATE,
}

AUDIENCE_LABELS = {
    ScheduledEvent.AUDIENCE_ALL: "Entire Team",
    ScheduledEvent.AUDIENCE_PLAYERS: "Players",
    ScheduledEvent.AUDIENCE_COACHES: "Coaches",
    ScheduledEvent.AUDIENCE_STAFF: "Staff",
    ScheduledEvent.AUDIENCE_PARENTS: "Parents",
    ScheduledEvent.AUDIENCE_MANAGERS: "Managers",
    ScheduledEvent.AUDIENCE_PRIVATE: "Only Me",
}


def _resolve_team_for_user(user):
    membership = TeamMembership.objects.select_related("team").filter(user=user, is_active=True).first()
    return membership.team if membership else None


def _user_role(user):
    return getattr(getattr(user, "profile", None), "role", None)


def _is_player(user):
    return _user_role(user) == AccountProfile.ROLE_PLAYER


def _is_manager(user):
    return _user_role(user) == AccountProfile.ROLE_MANAGER


def _is_coach(user):
    return _user_role(user) == AccountProfile.ROLE_COACH


def _is_coach_or_manager(user):
    return _user_role(user) in {AccountProfile.ROLE_COACH, AccountProfile.ROLE_MANAGER}


def _team_player_count(team):
    if team is None:
        return 0
    return TeamMembership.objects.filter(
        team=team,
        status=TeamMembership.STATUS_APPROVED,
        is_active=True,
        user__profile__role=AccountProfile.ROLE_PLAYER,
    ).count()


def _attendance_summary_for_event(event):
    counts = {
        EventAttendance.STATUS_ATTENDING: 0,
        EventAttendance.STATUS_MAYBE: 0,
        EventAttendance.STATUS_NOT_ATTENDING: 0,
    }
    for row in (
        EventAttendance.objects.filter(event=event)
        .values("status")
        .annotate(total=Count("id"))
    ):
        counts[row["status"]] = row["total"]

    total_players = _team_player_count(event.team)
    responded = sum(counts.values())
    pending = max(total_players - responded, 0)
    return {
        "total_players": total_players,
        "responded": responded,
        "pending": pending,
        "attending": counts[EventAttendance.STATUS_ATTENDING],
        "maybe": counts[EventAttendance.STATUS_MAYBE],
        "not_attending": counts[EventAttendance.STATUS_NOT_ATTENDING],
    }


def _attendance_badge_from_rate(rate):
    if rate >= 85:
        return "high"
    if rate >= 65:
        return "medium"
    return "low"


def _attendance_reason_label(reason):
    reason_labels = dict(EventAttendance.NOT_ATTENDING_REASON_CHOICES)
    return reason_labels.get(reason or "", "")


def _event_queryset_for_user(user):
    team = _resolve_team_for_user(user)
    if team is not None:
        return ScheduledEvent.objects.filter(Q(team=team) | Q(team__isnull=True, created_by=user)).distinct()
    return ScheduledEvent.objects.filter(created_by=user)


def _parse_audience_targets(raw_targets):
    valid_targets = set(AUDIENCE_MAP.keys())
    cleaned = []
    for target in raw_targets:
        value = (target or "").strip().lower()
        if value in valid_targets and value not in cleaned:
            cleaned.append(value)

    if not cleaned:
        return [ScheduledEvent.AUDIENCE_ALL]
    if ScheduledEvent.AUDIENCE_PRIVATE in cleaned:
        return [ScheduledEvent.AUDIENCE_PRIVATE]
    if ScheduledEvent.AUDIENCE_ALL in cleaned:
        return [ScheduledEvent.AUDIENCE_ALL]
    return cleaned


def _audience_targets_from_stored(value):
    return _parse_audience_targets((value or ScheduledEvent.AUDIENCE_ALL).split(","))


def _audience_targets_to_stored(targets):
    return ",".join(_parse_audience_targets(targets))


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


def _is_event_visible_to_user(event, user):
    user_team = _resolve_team_for_user(user)
    if event.team is not None and (user_team is None or event.team_id != user_team.id):
        return False

    if event.created_by_id == user.id:
        return True

    targets = _audience_targets_from_stored(event.audience)
    if ScheduledEvent.AUDIENCE_PRIVATE in targets:
        return False
    if ScheduledEvent.AUDIENCE_ALL in targets:
        return True

    # Managers always see all events (except private)
    role = getattr(getattr(user, "profile", None), "role", None)
    if role == AccountProfile.ROLE_MANAGER:
        return True

    target = _viewer_target(user)
    return target in targets if target else False


def _parse_datetime(date_str, time_str):
    if not date_str or not time_str:
        return None
    try:
        raw_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return timezone.make_aware(raw_dt, timezone.get_current_timezone())


def _build_upcoming_events_context(user):
    now = timezone.now()
    event_qs = _event_queryset_for_user(user).filter(
        status=ScheduledEvent.STATUS_SCHEDULED,
        scheduled_at__gte=now,
    ).order_by("scheduled_at")
    visible_events = [event for event in event_qs if _is_event_visible_to_user(event, user)]
    upcoming_events = visible_events[:3]

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)

    monthly_events = [
        event
        for event in visible_events
        if month_start <= event.scheduled_at < next_month_start
    ]

    return {
        "upcoming_events": upcoming_events,
        "monthly_total": len(monthly_events),
        "monthly_matches": sum(1 for event in monthly_events if event.event_type == ScheduledEvent.TYPE_MATCH),
        "monthly_practices": sum(1 for event in monthly_events if event.event_type == ScheduledEvent.TYPE_PRACTICE),
    }


def _tone_class_for_event_type(event_type):
    return {
        ScheduledEvent.TYPE_MATCH: "tone-match",
        ScheduledEvent.TYPE_PRACTICE: "tone-practice",
        ScheduledEvent.TYPE_TOURNAMENT: "tone-tournament",
        ScheduledEvent.TYPE_TRAINING: "tone-training",
    }.get(event_type, "tone-other")


def _get_conflict_queryset(user, *, exclude_id=None):
    team = _resolve_team_for_user(user)
    if team is not None:
        qs = ScheduledEvent.objects.filter(team=team, status=ScheduledEvent.STATUS_SCHEDULED)
    else:
        qs = ScheduledEvent.objects.filter(
            team__isnull=True,
            created_by=user,
            status=ScheduledEvent.STATUS_SCHEDULED,
        )
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs


def _has_overlap_conflict(user, scheduled_at, duration_minutes, *, exclude_id=None):
    new_start = scheduled_at
    new_end = scheduled_at + timedelta(minutes=max(1, duration_minutes))
    for existing in _get_conflict_queryset(user, exclude_id=exclude_id):
        existing_start = existing.scheduled_at
        existing_end = existing_start + timedelta(minutes=max(1, existing.duration_minutes))
        if new_start < existing_end and new_end > existing_start:
            return True
    return False


def _can_manage_event(user, event):
    # Only coaches, staff, and managers can edit/delete events
    role = getattr(getattr(user, "profile", None), "role", None)
    if role not in {AccountProfile.ROLE_COACH, AccountProfile.ROLE_STAFF, AccountProfile.ROLE_MANAGER}:
        return False
    
    actor_team = _resolve_team_for_user(user)
    if event.team_id is not None and actor_team is not None and event.team_id == actor_team.id:
        return True
    if event.team_id is None and event.created_by_id == user.id:
        return True
    return False


def _can_user_create_events(user):
    """Check if user has permission to create events (coaches, managers, staff only)"""
    if not getattr(user, "is_authenticated", False):
        return False
    
    profile = getattr(user, "profile", None)
    if not profile:
        return False
    
    allowed_roles = {
        AccountProfile.ROLE_COACH,
        AccountProfile.ROLE_MANAGER,
        AccountProfile.ROLE_STAFF,
    }
    return profile.role in allowed_roles


def _parse_month_param(value, now):
    if not value:
        return now.year, now.month
    try:
        year_str, month_str = value.split("-", 1)
        year = int(year_str)
        month = int(month_str)
        if 1 <= month <= 12:
            return year, month
    except (ValueError, TypeError):
        pass
    return now.year, now.month


def _month_bounds(year, month, tz):
    start = timezone.make_aware(datetime(year, month, 1, 0, 0), tz)
    if month == 12:
        next_start = timezone.make_aware(datetime(year + 1, 1, 1, 0, 0), tz)
    else:
        next_start = timezone.make_aware(datetime(year, month + 1, 1, 0, 0), tz)
    return start, next_start


def _event_type_badge(event_type):
    return {
        ScheduledEvent.TYPE_MATCH: "Match",
        ScheduledEvent.TYPE_PRACTICE: "Practice",
        ScheduledEvent.TYPE_TOURNAMENT: "Tournament",
        ScheduledEvent.TYPE_TRAINING: "Strength Training",
    }.get(event_type, "Other")


def _audience_label(audience):
    targets = _audience_targets_from_stored(audience)
    return ", ".join(AUDIENCE_LABELS.get(target, target.title()) for target in targets)


def _serialize_event(event):
    local_start = timezone.localtime(event.scheduled_at)
    local_end = local_start + timedelta(minutes=max(1, event.duration_minutes))
    
    # Determine event status
    now = timezone.localtime(timezone.now())
    if local_end < now:
        status = "past"
    elif local_start > now:
        status = "upcoming"
    else:
        status = "pending"
    
    return {
        "id": event.id,
        "title": event.title,
        "start_time": local_start.strftime("%I:%M %p"),
        "end_time": local_end.strftime("%I:%M %p"),
        "when": local_start.strftime("%a, %d %b %Y"),
        "scheduled_at": event.scheduled_at,
        "location": event.location,
        "details": event.details,
        "attendees_count": event.attendees_count,
        "duration_minutes": event.duration_minutes,
        "audience_label": _audience_label(event.audience),
        "event_date": local_start.date().isoformat(),
        "start_local_iso": local_start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end_local_iso": local_end.strftime("%Y-%m-%dT%H:%M:%S"),
        "badge": _event_type_badge(event.event_type),
        "event_type": event.event_type,
        "status": status,
    }


def _build_calendar_context(user, ym_value=None):
    now = timezone.localtime(timezone.now())
    tz = timezone.get_current_timezone()
    year, month = _parse_month_param(ym_value, now)
    month_start, next_month_start = _month_bounds(year, month, tz)

    monthly_events_qs = list(
        _event_queryset_for_user(user)
        .filter(
            status=ScheduledEvent.STATUS_SCHEDULED,
            scheduled_at__gte=month_start,
            scheduled_at__lt=next_month_start,
        )
        .order_by("scheduled_at")
    )
    visible_month_events = [event for event in monthly_events_qs if _is_event_visible_to_user(event, user)]

    event_map = {}
    for event in visible_month_events:
        event_day = timezone.localtime(event.scheduled_at).date()
        event_map.setdefault(event_day, []).append(event)

    month_calendar = calendar.Calendar(firstweekday=6)
    calendar_weeks = []
    for week in month_calendar.monthdatescalendar(year, month):
        week_cells = []
        for day in week:
            day_events_raw = event_map.get(day, [])
            day_events = [_serialize_event(item) for item in day_events_raw]
            week_cells.append(
                {
                    "iso": day.isoformat(),
                    "day_label": str(day.day),
                    "is_current_month": day.month == month,
                    "events": day_events[:2],
                    "extra_count": max(0, len(day_events_raw) - 2),
                    "tone_class": _tone_class_for_event_type(day_events_raw[0].event_type) if day_events_raw else "",
                }
            )
        calendar_weeks.append(week_cells)

    player_attendance_map = {}
    if _is_player(user):
        player_attendance_map = {
            row.event_id: row
            for row in EventAttendance.objects.filter(
                event__in=visible_month_events,
                player=user,
            )
        }

    month_events = []
    for event in visible_month_events:
        serialized = _serialize_event(event)
        serialized["can_manage"] = _can_manage_event(user, event)
        attendance = player_attendance_map.get(event.id)
        serialized["current_user_rsvp"] = attendance.status if attendance else ""
        serialized["current_user_not_attending_reason"] = attendance.not_attending_reason if attendance else ""
        serialized["current_user_not_attending_reason_note"] = attendance.not_attending_reason_note if attendance else ""
        month_events.append(serialized)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return {
        "current_month_label": month_start.strftime("%B %Y"),
        "week_days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "calendar_weeks": calendar_weeks,
        "month_events": month_events,
        "selected_ym": f"{year:04d}-{month:02d}",
        "prev_ym": f"{prev_year:04d}-{prev_month:02d}",
        "next_ym": f"{next_year:04d}-{next_month:02d}",
    }


@login_required
def event_create_view(request):
    if not _can_user_create_events(request.user):
        messages.error(request, "You don't have permission to create events. Only coaches, managers, and staff can create events.")
        return redirect("scheduling:home")
    
    form_data = {
        "title": "",
        "event_type": "practice",
        "date": "",
        "time": "",
        "location": "",
        "attendees_count": "",
        "duration_minutes": "90",
        "audience": ["all"],
        "details": "",
    }

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        event_type_input = (request.POST.get("event_type") or "").strip().lower()
        date_str = (request.POST.get("date") or "").strip()
        time_str = (request.POST.get("time") or "").strip()
        location = (request.POST.get("location") or "").strip()
        details = (request.POST.get("details") or "").strip()
        attendees_count_raw = (request.POST.get("attendees_count") or "").strip()
        duration_minutes_raw = (request.POST.get("duration_minutes") or "").strip()
        audience_targets = _parse_audience_targets(request.POST.getlist("audience"))

        form_data.update(
            {
                "title": title,
                "event_type": event_type_input or "practice",
                "date": date_str,
                "time": time_str,
                "location": location,
                "attendees_count": attendees_count_raw,
                "duration_minutes": duration_minutes_raw or "90",
                "audience": audience_targets,
                "details": details,
            }
        )

        if not title:
            messages.error(request, "Please enter an event title.")
        else:
            scheduled_at = _parse_datetime(date_str, time_str)
            if scheduled_at is None:
                messages.error(request, "Please provide a valid date and time.")
            elif scheduled_at < timezone.now():
                messages.error(request, "Event date and time must be in the future.")
            else:
                try:
                    attendees_count = max(0, int(attendees_count_raw or "0"))
                except ValueError:
                    attendees_count = 0

                try:
                    duration_minutes = max(1, int(duration_minutes_raw or "90"))
                except ValueError:
                    duration_minutes = 90

                if _has_overlap_conflict(request.user, scheduled_at, duration_minutes):
                    messages.error(request, "This event overlaps with another scheduled event.")
                    context = _build_upcoming_events_context(request.user)
                    now_local = timezone.localtime(timezone.now())
                    context["min_date"] = now_local.date().isoformat()
                    context["form_title"] = "Create New Event"
                    context["submit_label"] = "Create Event"
                    context["form_action"] = reverse("scheduling:event_create")
                    context["form_data"] = form_data
                    context["cancel_url"] = reverse("scheduling:home")
                    return render(request, "scheduling/event_create.html", context)

                event_type = EVENT_TYPE_MAP.get(event_type_input, ScheduledEvent.TYPE_OTHER)
                audience = _audience_targets_to_stored(audience_targets)
                team = _resolve_team_for_user(request.user)
                ScheduledEvent.objects.create(
                    team=team,
                    created_by=request.user,
                    title=title,
                    event_type=event_type,
                    scheduled_at=scheduled_at,
                    location=location,
                    details=details,
                    attendees_count=attendees_count,
                    duration_minutes=duration_minutes,
                    audience=audience,
                )
                messages.success(request, "Event scheduled successfully.")
                return redirect("scheduling:home")

    context = _build_upcoming_events_context(request.user)
    now_local = timezone.localtime(timezone.now())
    context["min_date"] = now_local.date().isoformat()
    context["form_title"] = "Create New Event"
    context["submit_label"] = "Create Event"
    context["form_action"] = reverse("scheduling:event_create")
    context["form_data"] = form_data
    context["cancel_url"] = reverse("scheduling:home")
    return render(request, "scheduling/event_create.html", context)


@login_required
def calendar_view(request):
    context = _build_calendar_context(request.user, request.GET.get("ym"))
    context.update(build_user_notifications_context(request.user))
    context["can_create_events"] = _can_user_create_events(request.user)
    context["is_player"] = _is_player(request.user)
    context["can_view_attendance_dashboard"] = _is_manager(request.user)
    context["can_view_coach_upcoming"] = _is_coach_or_manager(request.user)
    context["not_attending_reason_options"] = [
        {"value": value, "label": label}
        for value, label in EventAttendance.NOT_ATTENDING_REASON_CHOICES
    ]
    return render(request, "scheduling/calendar.html", context)


@login_required
def attendance_dashboard_view(request):
    if not _is_manager(request.user):
        messages.error(request, "Only team managers can access attendance dashboard.")
        return redirect("scheduling:home")

    context = {
        "selected_ym": request.GET.get("ym", ""),
    }
    context.update(build_user_notifications_context(request.user))
    return render(request, "scheduling/attendance_dashboard.html", context)


@login_required
def notifications_view(request):
    notifications = get_user_notifications(request.user)
    unread_count = sum(1 for item in notifications if item["is_unread"])
    return render(
        request,
        "scheduling/notifications.html",
        {
            "notifications": notifications,
            "notification_unread_count": unread_count,
        },
    )


@login_required
@require_POST
def mark_notification_read_view(request, event_id):
    notifications = get_user_notifications(request.user)
    target = next((item for item in notifications if item["event_id"] == event_id), None)
    if target and target["is_unread"]:
        mark_notifications_read(request.user, [event_id])
        messages.success(request, "Notification marked as read.")
    next_url = (request.POST.get("next") or "").strip()
    if next_url:
        return redirect(next_url)
    return redirect("scheduling:notifications")


@login_required
@require_POST
def mark_all_notifications_read_view(request):
    updated_count = mark_all_user_notifications_read(request.user)
    if updated_count:
        messages.success(request, "Notifications marked as read.")
    next_url = (request.POST.get("next") or "").strip()
    if next_url:
        return redirect(next_url)
    return redirect("scheduling:notifications")


@login_required
@require_POST
def open_notification_event_view(request, event_id):
    visible_event_ids = {item["event_id"] for item in get_user_notifications(request.user)}
    if event_id in visible_event_ids:
        mark_notifications_read(request.user, [event_id])

    next_url = (request.POST.get("next") or "").strip()
    if next_url:
        return redirect(next_url)
    return redirect("scheduling:home")


@login_required
@require_POST
def delete_notification_view(request, event_id):
    visible_event_ids = {item["event_id"] for item in get_user_notifications(request.user)}
    if event_id in visible_event_ids:
        delete_notifications(request.user, [event_id])
        messages.success(request, "Notification deleted.")

    next_url = (request.POST.get("next") or "").strip()
    if next_url:
        return redirect(next_url)
    return redirect("scheduling:notifications")


@login_required
@require_POST
def delete_announcement_notification_view(request, announcement_id):
    visible_announcement_ids = {
        item["announcement_id"]
        for item in get_user_notifications(request.user)
        if item.get("announcement_id")
    }
    if announcement_id in visible_announcement_ids:
        delete_announcement_notifications(request.user, [announcement_id])
        messages.success(request, "Notification deleted.")

    next_url = (request.POST.get("next") or "").strip()
    if next_url:
        return redirect(next_url)
    return redirect("scheduling:notifications")


@login_required
def edit_event_view(request, event_id):
    if not _can_user_create_events(request.user):
        messages.error(request, "You don't have permission to edit events. Only coaches, managers, and staff can edit events.")
        return redirect("scheduling:home")
    
    event = ScheduledEvent.objects.filter(id=event_id).first()
    if event is None:
        messages.error(request, "This event was already removed.")
        return redirect("scheduling:home")
    if not _can_manage_event(request.user, event):
        messages.error(request, "You are not allowed to edit this event.")
        return redirect("scheduling:home")

    ym = (request.GET.get("ym") or request.POST.get("ym") or "").strip()
    now_local = timezone.localtime(timezone.now())
    local_event_dt = timezone.localtime(event.scheduled_at)
    form_data = {
        "title": event.title,
        "event_type": event.event_type,
        "date": local_event_dt.date().isoformat(),
        "time": local_event_dt.strftime("%H:%M"),
        "location": event.location,
        "attendees_count": str(event.attendees_count),
        "duration_minutes": str(event.duration_minutes),
        "audience": _audience_targets_from_stored(event.audience),
        "details": event.details,
    }

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        event_type_input = (request.POST.get("event_type") or "").strip().lower()
        date_str = (request.POST.get("date") or "").strip()
        time_str = (request.POST.get("time") or "").strip()
        location = (request.POST.get("location") or "").strip()
        details = (request.POST.get("details") or "").strip()
        attendees_count_raw = (request.POST.get("attendees_count") or "").strip()
        duration_minutes_raw = (request.POST.get("duration_minutes") or "").strip()
        audience_targets = _parse_audience_targets(request.POST.getlist("audience"))

        form_data.update(
            {
                "title": title,
                "event_type": event_type_input or "practice",
                "date": date_str,
                "time": time_str,
                "location": location,
                "attendees_count": attendees_count_raw,
                "duration_minutes": duration_minutes_raw or "90",
                "audience": audience_targets,
                "details": details,
            }
        )

        if not title:
            messages.error(request, "Please enter an event title.")
        else:
            scheduled_at = _parse_datetime(date_str, time_str)
            if scheduled_at is None:
                messages.error(request, "Please provide a valid date and time.")
            elif scheduled_at < timezone.now():
                messages.error(request, "Event date and time must be in the future.")
            else:
                try:
                    attendees_count = max(0, int(attendees_count_raw or "0"))
                except ValueError:
                    attendees_count = 0

                try:
                    duration_minutes = max(1, int(duration_minutes_raw or "90"))
                except ValueError:
                    duration_minutes = 90

                if _has_overlap_conflict(request.user, scheduled_at, duration_minutes, exclude_id=event.id):
                    messages.error(request, "This event overlaps with another scheduled event.")
                    context = _build_upcoming_events_context(request.user)
                    context["min_date"] = now_local.date().isoformat()
                    context["form_title"] = "Edit Event"
                    context["submit_label"] = "Save Changes"
                    context["form_action"] = reverse("scheduling:edit_event", kwargs={"event_id": event.id})
                    context["form_data"] = form_data
                    context["is_edit"] = True
                    context["selected_ym"] = ym
                    context["cancel_url"] = f"{reverse('scheduling:home')}?ym={ym}" if ym else reverse("scheduling:home")
                    return render(request, "scheduling/event_create.html", context)

                event.title = title
                event.event_type = EVENT_TYPE_MAP.get(event_type_input, ScheduledEvent.TYPE_OTHER)
                event.scheduled_at = scheduled_at
                event.location = location
                event.details = details
                event.attendees_count = attendees_count
                event.duration_minutes = duration_minutes
                event.audience = _audience_targets_to_stored(audience_targets)
                event.save(update_fields=[
                    "title",
                    "event_type",
                    "scheduled_at",
                    "location",
                    "details",
                    "attendees_count",
                    "duration_minutes",
                    "audience",
                    "updated_at",
                ])
                messages.success(request, "Event updated successfully.")
                if ym:
                    return redirect(f"{reverse('scheduling:home')}?ym={ym}")
                return redirect("scheduling:home")

    context = _build_upcoming_events_context(request.user)
    context["min_date"] = now_local.date().isoformat()
    context["form_title"] = "Edit Event"
    context["submit_label"] = "Save Changes"
    context["form_action"] = reverse("scheduling:edit_event", kwargs={"event_id": event.id})
    context["form_data"] = form_data
    context["is_edit"] = True
    context["selected_ym"] = ym
    context["cancel_url"] = f"{reverse('scheduling:home')}?ym={ym}" if ym else reverse("scheduling:home")
    return render(request, "scheduling/event_create.html", context)


@login_required
@require_POST
def delete_event_view(request, event_id):
    event = ScheduledEvent.objects.filter(id=event_id).first()
    if event is None:
        messages.error(request, "This event was already removed.")
    elif not _can_manage_event(request.user, event):
        messages.error(request, "You are not allowed to delete this event.")
    else:
        event.delete()
        messages.success(request, "Event deleted successfully.")

    ym = (request.POST.get("ym") or "").strip()
    if ym:
        return redirect(f"{reverse('scheduling:home')}?ym={ym}")
    return redirect("scheduling:home")


@login_required
def event_detail_view(request, event_id):
    """Return event details as JSON for modal display"""
    try:
        event = ScheduledEvent.objects.filter(id=event_id).first()
        if event is None:
            return JsonResponse({"error": "Event not found"}, status=404)
        
        # Check if user can see this event based on audience/permissions
        if not _is_event_visible_to_user(event, request.user):
            return JsonResponse({"error": "You don't have access to this event"}, status=403)
        
        # Serialize event data
        serialized = _serialize_event(event)
        serialized["created_by"] = event.created_by.get_full_name() or event.created_by.username
        serialized["attendance_summary"] = _attendance_summary_for_event(event)

        if _is_player(request.user):
            attendance = EventAttendance.objects.filter(event=event, player=request.user).first()
            serialized["current_user_rsvp"] = attendance.status if attendance else ""
            serialized["current_user_not_attending_reason"] = attendance.not_attending_reason if attendance else ""
            serialized["current_user_not_attending_reason_note"] = attendance.not_attending_reason_note if attendance else ""
            serialized["current_user_not_attending_reason_label"] = _attendance_reason_label(
                attendance.not_attending_reason if attendance else ""
            )
        else:
            serialized["current_user_rsvp"] = ""
            serialized["current_user_not_attending_reason"] = ""
            serialized["current_user_not_attending_reason_note"] = ""
            serialized["current_user_not_attending_reason_label"] = ""
        
        return JsonResponse(serialized)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": f"Server error: {str(e)}"}, status=500)


@login_required
@require_POST
def player_event_rsvp_view(request, event_id):
    event = get_object_or_404(ScheduledEvent, id=event_id)

    if not _is_event_visible_to_user(event, request.user):
        return JsonResponse({"error": "You don't have access to this event"}, status=403)

    if not _is_player(request.user):
        return JsonResponse({"error": "Only players can submit attendance RSVP."}, status=403)

    membership = TeamMembership.objects.filter(
        user=request.user,
        team=event.team,
        status=TeamMembership.STATUS_APPROVED,
        is_active=True,
    ).first()
    if event.team is None or membership is None:
        return JsonResponse({"error": "You are not an active player in this event's team."}, status=403)

    status = (request.POST.get("status") or "").strip().lower()
    valid_statuses = {
        EventAttendance.STATUS_ATTENDING,
        EventAttendance.STATUS_MAYBE,
        EventAttendance.STATUS_NOT_ATTENDING,
    }
    if status not in valid_statuses:
        return JsonResponse({"error": "Invalid RSVP status."}, status=400)

    reason = (request.POST.get("not_attending_reason") or "").strip().lower()
    reason_note = (request.POST.get("not_attending_reason_note") or "").strip()
    valid_reasons = {value for value, _ in EventAttendance.NOT_ATTENDING_REASON_CHOICES}
    if status == EventAttendance.STATUS_NOT_ATTENDING:
        if reason not in valid_reasons:
            return JsonResponse(
                {
                    "error": "Please select a not attending reason.",
                    "valid_reasons": [
                        {"value": value, "label": label}
                        for value, label in EventAttendance.NOT_ATTENDING_REASON_CHOICES
                    ],
                },
                status=400,
            )
        if reason == EventAttendance.REASON_OTHER and not reason_note:
            return JsonResponse({"error": "Please provide the other reason details."}, status=400)
    else:
        reason = ""
        reason_note = ""

    if reason != EventAttendance.REASON_OTHER:
        reason_note = ""

    attendance, _ = EventAttendance.objects.update_or_create(
        event=event,
        player=request.user,
        defaults={
            "status": status,
            "not_attending_reason": reason,
            "not_attending_reason_note": reason_note,
        },
    )

    return JsonResponse(
        {
            "ok": True,
            "event_id": event.id,
            "status": attendance.status,
            "not_attending_reason": attendance.not_attending_reason,
            "not_attending_reason_label": _attendance_reason_label(attendance.not_attending_reason),
            "not_attending_reason_note": attendance.not_attending_reason_note,
            "summary": _attendance_summary_for_event(event),
        }
    )


@login_required
def manager_attendance_stats_view(request):
    if not _is_manager(request.user):
        return JsonResponse({"error": "Only managers can access attendance stats."}, status=403)

    team = _resolve_team_for_user(request.user)
    if team is None:
        return JsonResponse({"players": [], "meta": {"total_events": 0, "returned": 0}})

    event_qs = ScheduledEvent.objects.filter(team=team, status=ScheduledEvent.STATUS_SCHEDULED)
    event_type = (request.GET.get("event_type") or "").strip().lower()
    if event_type in EVENT_TYPE_MAP:
        event_qs = event_qs.filter(event_type=EVENT_TYPE_MAP[event_type])

    total_events = event_qs.count()
    memberships = list(
        TeamMembership.objects.select_related("user")
        .filter(
            team=team,
            status=TeamMembership.STATUS_APPROVED,
            is_active=True,
            user__profile__role=AccountProfile.ROLE_PLAYER,
        )
        .order_by("user__first_name", "user__last_name", "user__email")
    )
    player_ids = [membership.user_id for membership in memberships]

    attendance_counts = {}
    for row in (
        EventAttendance.objects.filter(event__in=event_qs, player_id__in=player_ids)
        .values("player_id", "status")
        .annotate(total=Count("id"))
    ):
        player_bucket = attendance_counts.setdefault(row["player_id"], {
            EventAttendance.STATUS_ATTENDING: 0,
            EventAttendance.STATUS_MAYBE: 0,
            EventAttendance.STATUS_NOT_ATTENDING: 0,
        })
        player_bucket[row["status"]] = row["total"]

    rows = []
    for membership in memberships:
        user = membership.user
        counts = attendance_counts.get(
            user.id,
            {
                EventAttendance.STATUS_ATTENDING: 0,
                EventAttendance.STATUS_MAYBE: 0,
                EventAttendance.STATUS_NOT_ATTENDING: 0,
            },
        )
        responded = sum(counts.values())
        pending = max(total_events - responded, 0)
        attendance_rate = round((counts[EventAttendance.STATUS_ATTENDING] / max(total_events, 1)) * 100, 1) if total_events else 0.0
        response_rate = round((responded / max(total_events, 1)) * 100, 1) if total_events else 0.0
        rows.append(
            {
                "player_id": user.id,
                "name": user.get_full_name() or user.email,
                "position": getattr(getattr(user, "profile", None), "position", "") or "Player",
                "attending": counts[EventAttendance.STATUS_ATTENDING],
                "maybe": counts[EventAttendance.STATUS_MAYBE],
                "not_attending": counts[EventAttendance.STATUS_NOT_ATTENDING],
                "responded": responded,
                "pending": pending,
                "attendance_rate": attendance_rate,
                "response_rate": response_rate,
                "status_bucket": _attendance_badge_from_rate(attendance_rate),
            }
        )

    query = (request.GET.get("q") or "").strip().lower()
    status_filter = (request.GET.get("status") or "all").strip().lower()
    min_rate = request.GET.get("min_rate")
    max_rate = request.GET.get("max_rate")

    if query:
        rows = [row for row in rows if query in row["name"].lower() or query in row["position"].lower()]

    if status_filter in {"high", "medium", "low"}:
        rows = [row for row in rows if row["status_bucket"] == status_filter]

    try:
        if min_rate is not None and str(min_rate).strip() != "":
            min_rate_val = float(min_rate)
            rows = [row for row in rows if row["attendance_rate"] >= min_rate_val]
    except ValueError:
        pass

    try:
        if max_rate is not None and str(max_rate).strip() != "":
            max_rate_val = float(max_rate)
            rows = [row for row in rows if row["attendance_rate"] <= max_rate_val]
    except ValueError:
        pass

    sort_key = (request.GET.get("sort") or "attendance_rate").strip()
    direction = (request.GET.get("dir") or "desc").strip().lower()
    reverse = direction != "asc"
    allowed_sort_keys = {
        "name": lambda row: row["name"].lower(),
        "position": lambda row: row["position"].lower(),
        "attendance_rate": lambda row: row["attendance_rate"],
        "response_rate": lambda row: row["response_rate"],
        "attending": lambda row: row["attending"],
        "responded": lambda row: row["responded"],
        "pending": lambda row: row["pending"],
    }
    rows.sort(key=allowed_sort_keys.get(sort_key, allowed_sort_keys["attendance_rate"]), reverse=reverse)

    return JsonResponse(
        {
            "players": rows,
            "meta": {
                "team": team.name,
                "total_events": total_events,
                "returned": len(rows),
                "sort": sort_key,
                "dir": "desc" if reverse else "asc",
                "filters": {
                    "q": query,
                    "status": status_filter,
                    "min_rate": min_rate,
                    "max_rate": max_rate,
                    "event_type": event_type,
                },
            },
        }
    )


@login_required
def coach_upcoming_events_view(request):
    if not _is_coach_or_manager(request.user):
        return JsonResponse({"error": "Only coaches and managers can access upcoming events feed."}, status=403)

    team = _resolve_team_for_user(request.user)
    if team is None:
        return JsonResponse({"events": [], "count": 0})

    try:
        limit = int((request.GET.get("limit") or "5").strip())
    except ValueError:
        limit = 5
    limit = max(1, min(20, limit))

    now = timezone.now()
    events = (
        ScheduledEvent.objects.filter(
            team=team,
            status=ScheduledEvent.STATUS_SCHEDULED,
            scheduled_at__gte=now,
        )
        .order_by("scheduled_at")[:limit]
    )

    payload = []
    for event in events:
        serialized = _serialize_event(event)
        payload.append(
            {
                "id": serialized["id"],
                "title": serialized["title"],
                "when": serialized["when"],
                "start_time": serialized["start_time"],
                "end_time": serialized["end_time"],
                "location": serialized["location"],
                "event_type": serialized["event_type"],
                "badge": serialized["badge"],
                "attendees_count": serialized["attendees_count"],
            }
        )

    return JsonResponse({"events": payload, "count": len(payload), "limit": limit})
