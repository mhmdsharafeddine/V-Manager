from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, When
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.models import AccountProfile
from team_management.models import TeamMembership

from .forms import AnnouncementComposeForm
from .models import (
    Announcement,
    AnnouncementMessage,
    AnnouncementRecipient,
    UserPresence,
    UserPresenceConnection,
)
from .notification_delivery import dispatch_announcement_notifications


def _resolve_active_team(user):
    membership = TeamMembership.objects.select_related("team").filter(
        user=user,
        is_active=True,
        status=TeamMembership.STATUS_APPROVED,
    ).first()
    return membership.team if membership else None


def _can_user_post_announcements(user):
    profile = getattr(user, "profile", None)
    if profile is None:
        return False

    return profile.role in {
        AccountProfile.ROLE_COACH,
        AccountProfile.ROLE_MANAGER,
        AccountProfile.ROLE_STAFF,
    }


def _can_user_pin_announcements(user):
    profile = getattr(user, "profile", None)
    return bool(profile and profile.role == AccountProfile.ROLE_MANAGER)


def _target_role_for_audience(audience):
    return {
        Announcement.AUDIENCE_PLAYERS: AccountProfile.ROLE_PLAYER,
        Announcement.AUDIENCE_COACHES: AccountProfile.ROLE_COACH,
        Announcement.AUDIENCE_STAFF: AccountProfile.ROLE_STAFF,
        Announcement.AUDIENCE_PARENTS: AccountProfile.ROLE_PARENT,
        Announcement.AUDIENCE_MANAGERS: AccountProfile.ROLE_MANAGER,
    }.get(audience)


def _recipient_user_ids_for_announcement(team, audience, sender_id):
    if team is None:
        return [sender_id]

    memberships = TeamMembership.objects.filter(
        team=team,
        is_active=True,
        status=TeamMembership.STATUS_APPROVED,
    )

    if audience != Announcement.AUDIENCE_ALL:
        role = _target_role_for_audience(audience)
        if role:
            memberships = memberships.filter(user__profile__role=role)

    user_ids = list(memberships.values_list("user_id", flat=True))
    if sender_id not in user_ids:
        user_ids.append(sender_id)
    return user_ids


def _build_week_start(now):
    local_now = timezone.localtime(now)
    return (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _pin_expiration_cutoff(now=None):
    now = now or timezone.now()
    return now - timedelta(days=2)


def _expire_old_pins(team):
    if team is None:
        return

    Announcement.objects.filter(
        team=team,
        pin_to_top=True,
        created_at__lt=_pin_expiration_cutoff(),
    ).update(pin_to_top=False)


def _format_duration_from_seconds(total_seconds):
    if total_seconds is None:
        return None

    if total_seconds < 60:
        return "<1m"

    total_minutes = int(round(total_seconds / 60.0))
    hours, minutes = divmod(total_minutes, 60)

    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _announcement_queryset_for_user(user, team):
    pin_cutoff = _pin_expiration_cutoff()

    if team is None:
        base_queryset = Announcement.objects.filter(created_by=user)
    else:
        base_queryset = (
            Announcement.objects.filter(team=team)
            .filter(Q(recipients__user=user) | Q(created_by=user))
            .distinct()
        )

    return base_queryset.annotate(
        is_active_pin=Case(
            When(pin_to_top=True, created_at__gte=pin_cutoff, then=1),
            default=0,
            output_field=IntegerField(),
        )
    )


def _announcement_feed_queryset(user, team):
    return (
        _announcement_queryset_for_user(user, team)
        .select_related("created_by")
        .annotate(
            read_count=Count("recipients", filter=Q(recipients__read_at__isnull=False), distinct=True),
        )
        .order_by("-is_active_pin", "-created_at", "-id")
    )


def _build_announcements_payload(user, queryset):
    items = list(queryset)
    announcement_ids = [item.id for item in items]
    recipient_map = {
        row["announcement_id"]: row["read_at"]
        for row in AnnouncementRecipient.objects.filter(
            user=user,
            announcement_id__in=announcement_ids,
        ).values("announcement_id", "read_at")
    }

    payload = []
    for item in items:
        is_read = bool(recipient_map.get(item.id))
        payload.append(
            {
                "id": item.id,
                "title": item.title,
                "body": item.body,
                "author_name": item.created_by.get_full_name() or item.created_by.email,
                "created_label": timezone.localtime(item.created_at).strftime("%b %d, %I:%M %p"),
                "view_count": item.read_count,
                "priority": item.priority,
                "is_read": is_read,
                "is_pinned": bool(item.is_active_pin),
                "badges": [
                    {
                        "label": item.get_priority_display(),
                        "variant": item.priority,
                    }
                ],
            }
        )

    return payload


def _build_recent_announcements(user, team, *, limit=5):
    queryset = _announcement_feed_queryset(user, team)[:limit]
    return _build_announcements_payload(user, queryset)


def _build_all_announcements(user, team):
    queryset = _announcement_feed_queryset(user, team)
    return _build_announcements_payload(user, queryset)


def _build_weekly_stats(user, team):
    if team is None:
        return {
            "announcements_sent": None,
            "average_read_rate": None,
        }

    week_start = _build_week_start(timezone.now())
    user_weekly_recipients = AnnouncementRecipient.objects.filter(
        user=user,
        announcement__team=team,
        announcement__created_at__gte=week_start,
    )

    weekly_count = user_weekly_recipients.values("announcement_id").distinct().count()
    if weekly_count == 0:
        return {
            "announcements_sent": None,
            "average_read_rate": None,
        }

    read_count = user_weekly_recipients.filter(read_at__isnull=False).count()
    average_read_rate = int(round((read_count * 100) / weekly_count))

    return {
        "announcements_sent": weekly_count,
        "average_read_rate": average_read_rate,
    }


def _calculate_average_response_time(team):
    if team is None:
        return None

    announcements = Announcement.objects.filter(team=team).prefetch_related("messages")
    durations = []
    for announcement in announcements:
        first_response = None
        for message in announcement.messages.all():
            if message.author_id != announcement.created_by_id:
                first_response = message
                break

        if first_response is not None:
            durations.append((first_response.created_at - announcement.created_at).total_seconds())

    if not durations:
        return None

    return _format_duration_from_seconds(sum(durations) / len(durations))


def _count_live_online_members(team):
    timeout_seconds = getattr(settings, "COMMUNICATION_PRESENCE_HEARTBEAT_TIMEOUT_SECONDS", 45)
    cutoff = timezone.now() - timedelta(seconds=timeout_seconds)
    return (
        UserPresenceConnection.objects.filter(
            team=team,
            disconnected_at__isnull=True,
            last_heartbeat_at__gte=cutoff,
        )
        .values("user_id")
        .distinct()
        .count()
    )


def _count_recent_activity_online_members(team):
    fallback_seconds = getattr(settings, "COMMUNICATION_PRESENCE_FALLBACK_WINDOW_SECONDS", 300)
    online_cutoff = timezone.now() - timedelta(seconds=fallback_seconds)
    return (
        UserPresence.objects.filter(
            last_seen__gte=online_cutoff,
            user__team_membership__team=team,
            user__team_membership__is_active=True,
            user__team_membership__status=TeamMembership.STATUS_APPROVED,
        )
        .values("user_id")
        .distinct()
        .count()
    )


def _build_quick_stats(user, team):
    if team is None:
        return {
            "total_members": None,
            "online_now": None,
            "unread_messages": None,
            "avg_response_time": None,
        }

    total_members = TeamMembership.objects.filter(
        team=team,
        is_active=True,
        status=TeamMembership.STATUS_APPROVED,
    ).count()

    live_online_now = _count_live_online_members(team)
    online_now = live_online_now if live_online_now > 0 else _count_recent_activity_online_members(team)

    visible_announcement_ids = list(_announcement_queryset_for_user(user, team).values_list("id", flat=True))
    unread_messages = 0
    if visible_announcement_ids:
        unread_messages = (
            AnnouncementMessage.objects.filter(announcement_id__in=visible_announcement_ids)
            .exclude(author=user)
            .exclude(read_states__user=user)
            .distinct()
            .count()
        )

    return {
        "total_members": total_members,
        "online_now": online_now,
        "unread_messages": unread_messages,
        "avg_response_time": _calculate_average_response_time(team),
    }


def _checkbox_value(data, key, *, default):
    raw = data.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "on", "yes"}


def _build_compose_state(form):
    defaults = {
        "title": "",
        "body": "",
        "audience": Announcement.AUDIENCE_ALL,
        "priority": Announcement.PRIORITY_INFO,
        "send_push_notification": True,
        "send_email_notification": True,
        "pin_to_top": False,
    }
    if not form.is_bound:
        return defaults

    data = form.data
    return {
        "title": data.get("title", ""),
        "body": data.get("body", ""),
        "audience": data.get("audience") or defaults["audience"],
        "priority": data.get("priority") or defaults["priority"],
        "send_push_notification": _checkbox_value(data, "send_push_notification", default=False),
        "send_email_notification": _checkbox_value(data, "send_email_notification", default=False),
        "pin_to_top": _checkbox_value(data, "pin_to_top", default=False),
    }


def _create_announcement_from_form(*, user, team, form):
    cleaned = form.cleaned_data
    recipient_ids = _recipient_user_ids_for_announcement(team, cleaned["audience"], user.id)
    can_pin = _can_user_pin_announcements(user)
    should_pin = bool(cleaned["pin_to_top"] and can_pin)

    with transaction.atomic():
        _expire_old_pins(team)
        if should_pin:
            Announcement.objects.filter(team=team, pin_to_top=True).update(pin_to_top=False)

        announcement = Announcement.objects.create(
            team=team,
            created_by=user,
            title=cleaned["title"],
            body=cleaned["body"],
            audience=cleaned["audience"],
            priority=cleaned["priority"],
            send_push_notification=cleaned["send_push_notification"],
            send_email_notification=cleaned["send_email_notification"],
            send_sms_notification=False,
            pin_to_top=should_pin,
        )

        now = timezone.now()
        AnnouncementRecipient.objects.bulk_create(
            [
                AnnouncementRecipient(
                    announcement=announcement,
                    user_id=recipient_id,
                    read_at=now if recipient_id == user.id else None,
                )
                for recipient_id in recipient_ids
            ]
        )

        notify_recipient_ids = [recipient_id for recipient_id in recipient_ids if recipient_id != user.id]
        if notify_recipient_ids:
            transaction.on_commit(
                lambda: dispatch_announcement_notifications(
                    announcement_id=announcement.id,
                    recipient_ids=notify_recipient_ids,
                )
            )


def _safe_next_url(request, fallback_url):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url


@login_required
def hub(request):
    team = _resolve_active_team(request.user)
    _expire_old_pins(team)

    can_post_announcements = _can_user_post_announcements(request.user) and team is not None
    can_pin_announcements = _can_user_pin_announcements(request.user)

    if request.method == "POST":
        compose_form = AnnouncementComposeForm(request.POST)
        if not can_post_announcements:
            compose_form.add_error(None, "Only active coaches, managers, and staff members can post announcements.")
        elif compose_form.is_valid():
            _create_announcement_from_form(user=request.user, team=team, form=compose_form)
            messages.success(request, "Announcement posted successfully.")
            return redirect(reverse("communication:hub"))
    else:
        compose_form = AnnouncementComposeForm()

    feed_queryset = _announcement_feed_queryset(request.user, team)
    recent_announcements = _build_announcements_payload(request.user, feed_queryset[:5])

    context = {
        "announcements": recent_announcements,
        "weekly_stats": _build_weekly_stats(request.user, team),
        "quick_stats": _build_quick_stats(request.user, team),
        "compose_form": compose_form,
        "compose_state": _build_compose_state(compose_form),
        "audience_choices": Announcement.AUDIENCE_CHOICES,
        "priority_choices": Announcement.PRIORITY_CHOICES,
        "can_post_announcements": can_post_announcements,
        "can_pin_announcements": can_pin_announcements,
        "all_announcements_count": feed_queryset.count(),
    }
    return render(request, "communication/hub.html", context)


@login_required
def announcements_page(request):
    team = _resolve_active_team(request.user)
    _expire_old_pins(team)

    announcements = _build_all_announcements(request.user, team)
    return render(
        request,
        "communication/all_announcements.html",
        {
            "announcements": announcements,
            "announcement_count": len(announcements),
        },
    )


@login_required
@require_POST
def mark_announcement_read(request, announcement_id):
    team = _resolve_active_team(request.user)

    filters = {
        "user": request.user,
        "announcement_id": announcement_id,
        "read_at__isnull": True,
    }
    if team is not None:
        filters["announcement__team"] = team

    AnnouncementRecipient.objects.filter(**filters).update(read_at=timezone.now())
    messages.success(request, "Announcement marked as read.")
    return redirect(_safe_next_url(request, reverse("communication:hub")))

