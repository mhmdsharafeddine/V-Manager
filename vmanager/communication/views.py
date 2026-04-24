import json as _json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, OuterRef, Prefetch, Q, Subquery, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_http_methods

from accounts.models import AccountProfile, NotificationPreferences
from scheduling.notifications import _announcement_meets_min_priority, _in_quiet_hours
from team_management.models import TeamMembership

from .forms import AnnouncementComposeForm
from .models import (
    Announcement,
    AnnouncementComment,
    AnnouncementMessage,
    AnnouncementRecipient,
    PrivateMessage,
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

    comment_counts = dict(
        AnnouncementComment.objects.filter(
            announcement_id__in=announcement_ids,
            parent__isnull=True,
        )
        .values("announcement_id")
        .annotate(cnt=Count("id"))
        .values_list("announcement_id", "cnt")
    )

    payload = []
    for item in items:
        is_read = bool(recipient_map.get(item.id))
        # Only badge non-default priorities so "Info" (default) doesn't clutter
        priority_badges = []
        if item.priority and item.priority != Announcement.PRIORITY_INFO:
            priority_badges.append({
                "label": item.get_priority_display(),
                "variant": item.priority,
            })
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
                "badges": priority_badges,
                "comment_count": comment_counts.get(item.id, 0),
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


def _calculate_avg_read_response_time(user, team):
    """Average time (created_at → read_at) across all recipients of announcements
    created by `user`.  Returns a formatted string or None if no data."""
    if team is None:
        return None

    from django.db.models import F, ExpressionWrapper, DurationField
    from django.db.models import Avg

    result = (
        AnnouncementRecipient.objects.filter(
            announcement__team=team,
            announcement__created_by=user,
            read_at__isnull=False,
        )
        .exclude(user=user)  # exclude the sender's own auto-read record
        .annotate(
            read_duration=ExpressionWrapper(
                F("read_at") - F("announcement__created_at"),
                output_field=DurationField(),
            )
        )
        .aggregate(avg_duration=Avg("read_duration"))
    )["avg_duration"]

    if result is None:
        return None

    return _format_duration_from_seconds(result.total_seconds())


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
    # Response-time stat is only meaningful for users who send announcements.
    is_sender = _can_user_post_announcements(user)

    if team is None:
        return {
            "total_members": None,
            "online_now": None,
            "unread_messages": None,
            "avg_response_time": None,
            "show_response_time": is_sender,
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

    avg_response_time = _calculate_avg_read_response_time(user, team) if is_sender else None

    return {
        "total_members": total_members,
        "online_now": online_now,
        "unread_messages": unread_messages,
        "avg_response_time": avg_response_time,
        "show_response_time": is_sender,
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

    _MAX_PINS = 3

    with transaction.atomic():
        _expire_old_pins(team)
        if should_pin:
            current_pins = list(
                Announcement.objects.filter(team=team, pin_to_top=True)
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            overflow = len(current_pins) - (_MAX_PINS - 1)
            if overflow > 0:
                Announcement.objects.filter(id__in=current_pins[:overflow]).update(pin_to_top=False)

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

        # ── Filter in-app delivery by recipient's notification preferences ──
        # Sender always gets their own announcement; non-senders are checked.
        non_sender_ids = [rid for rid in recipient_ids if rid != user.id]
        _prefs_map = {
            p.user_id: p
            for p in NotificationPreferences.objects.filter(user_id__in=non_sender_ids)
        } if non_sender_ids else {}

        def _inapp_delivery_allowed(rid):
            if rid == user.id:
                return True  # sender always gets it
            p = _prefs_map.get(rid)
            if p is None:
                return True  # no record = defaults (all on)
            if not p.push_enabled or not p.announcements_enabled:
                return False
            if not _announcement_meets_min_priority(
                announcement.priority, p.announcement_min_priority
            ):
                return False
            # Hard quiet-skip: don't create the record (notification dropped entirely)
            if _in_quiet_hours(p, now) and p.quiet_skip_entirely:
                return False
            return True

        inapp_recipient_ids = [rid for rid in recipient_ids if _inapp_delivery_allowed(rid)]

        AnnouncementRecipient.objects.bulk_create(
            [
                AnnouncementRecipient(
                    announcement=announcement,
                    user_id=recipient_id,
                    read_at=now if recipient_id == user.id else None,
                )
                for recipient_id in inapp_recipient_ids
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

    full_qs = _announcement_feed_queryset(request.user, team)

    # Build author filter options – only roles that can post announcements.
    sender_ids = list(full_qs.values_list("created_by_id", flat=True).distinct())
    User = get_user_model()
    senders = (
        User.objects.filter(id__in=sender_ids)
        .select_related("profile")
        .order_by("first_name", "last_name", "email")
    )
    author_filters = []
    for sender in senders:
        role = getattr(getattr(sender, "profile", None), "role", "")
        if role not in {AccountProfile.ROLE_COACH, AccountProfile.ROLE_MANAGER, AccountProfile.ROLE_STAFF}:
            continue
        author_filters.append({
            "id": sender.id,
            "name": sender.get_full_name() or sender.email,
            "role": role,
            "role_label": role.capitalize(),
        })

    # Apply ?author=<id> filter when present and valid.
    raw_author = request.GET.get("author", "").strip()
    active_author_id = None
    filtered_qs = full_qs
    if raw_author.isdigit():
        candidate_id = int(raw_author)
        if any(f["id"] == candidate_id for f in author_filters):
            active_author_id = candidate_id
            filtered_qs = full_qs.filter(created_by_id=active_author_id)

    announcements = _build_announcements_payload(request.user, filtered_qs)
    active_author_name = next(
        (f["name"] for f in author_filters if f["id"] == active_author_id), None
    )
    return render(
        request,
        "communication/all_announcements.html",
        {
            "announcements": announcements,
            "announcement_count": len(announcements),
            "author_filters": author_filters,
            "active_author_id": active_author_id,
            "active_author_name": active_author_name,
            "can_pin_announcements": _can_user_pin_announcements(request.user),
        },
    )


@login_required
@require_POST
def toggle_pin_announcement(request, announcement_id):
    """Pin or unpin an announcement. Managers only. Max 3 active pins."""
    if not _can_user_pin_announcements(request.user):
        messages.error(request, "Only managers can pin announcements.")
        return redirect(_safe_next_url(request, reverse("communication:announcements")))

    team = _resolve_active_team(request.user)
    announcement = get_object_or_404(
        Announcement,
        id=announcement_id,
        team=team,
    )

    with transaction.atomic():
        _expire_old_pins(team)
        if announcement.pin_to_top:
            announcement.pin_to_top = False
            announcement.save(update_fields=["pin_to_top"])
        else:
            _MAX_PINS = 3
            current_pins = list(
                Announcement.objects.filter(team=team, pin_to_top=True)
                .order_by("created_at")
                .values_list("id", flat=True)
            )
            overflow = len(current_pins) - (_MAX_PINS - 1)
            if overflow > 0:
                Announcement.objects.filter(id__in=current_pins[:overflow]).update(pin_to_top=False)
            announcement.pin_to_top = True
            announcement.save(update_fields=["pin_to_top"])

    return redirect(_safe_next_url(request, reverse("communication:announcements")))


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


# ---------------------------------------------------------------------------
# Comments API
# ---------------------------------------------------------------------------

_COMMENTS_PAGE_SIZE = 10


def _serialize_comment(comment, user_id, include_replies=True):
    profile = getattr(comment.author, "profile", None)
    replies_data = []
    if include_replies:
        replies_data = [
            _serialize_comment(r, user_id, include_replies=False)
            for r in comment.replies.all()
        ]
    return {
        "id": comment.id,
        "body": comment.body,
        "author_name": comment.author.get_full_name() or comment.author.email,
        "author_initials": getattr(profile, "initials", comment.author.email[:2].upper()),
        "author_role": getattr(profile, "role", ""),
        "created_label": timezone.localtime(comment.created_at).strftime("%b %d, %I:%M %p"),
        "reply_count": len(replies_data) if include_replies else 0,
        "replies": replies_data,
        "is_own": comment.author_id == user_id,
    }


@login_required
@require_http_methods(["GET", "POST"])
def api_announcement_comments(request, announcement_id):
    team = _resolve_active_team(request.user)
    # Guard: user must be able to see the announcement
    announcement = get_object_or_404(
        _announcement_queryset_for_user(request.user, team),
        id=announcement_id,
    )

    if request.method == "GET":
        # ── Realtime polling: ?after=<last_comment_id> ────────────────────
        after_raw = request.GET.get("after")
        if after_raw is not None:
            try:
                after_id = int(after_raw)
            except (TypeError, ValueError):
                return JsonResponse({"error": "Invalid after parameter."}, status=400)
            new_qs = (
                AnnouncementComment.objects.filter(
                    announcement=announcement,
                    id__gt=after_id,
                )
                .select_related("author__profile")
                .order_by("created_at", "id")
            )
            total = AnnouncementComment.objects.filter(
                announcement=announcement, parent__isnull=True
            ).count()
            comments_data = []
            for c in new_qs[:50]:
                cd = _serialize_comment(c, request.user.id, include_replies=False)
                cd["parent_id"] = c.parent_id
                comments_data.append(cd)
            return JsonResponse({"comments": comments_data, "total": total})

        # ── Pagination ────────────────────────────────────────────────────
        try:
            page = max(1, int(request.GET.get("page", 1) or 1))
        except (TypeError, ValueError):
            page = 1
        offset = (page - 1) * _COMMENTS_PAGE_SIZE

        qs = (
            AnnouncementComment.objects.filter(
                announcement=announcement,
                parent__isnull=True,
            )
            .select_related("author__profile")
            .prefetch_related(
                Prefetch(
                    "replies",
                    queryset=AnnouncementComment.objects.select_related(
                        "author__profile"
                    ).order_by("created_at", "id"),
                )
            )
            .order_by("created_at", "id")
        )
        total = qs.count()
        comments = list(qs[offset: offset + _COMMENTS_PAGE_SIZE])

        return JsonResponse({
            "comments": [_serialize_comment(c, request.user.id) for c in comments],
            "page": page,
            "has_next": (offset + _COMMENTS_PAGE_SIZE) < total,
            "total": total,
        })

    # POST – create comment or reply
    try:
        payload = _json.loads(request.body)
    except (_json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    body = (payload.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "Comment body is required."}, status=400)
    if len(body) > 2000:
        return JsonResponse({"error": "Comment is too long (max 2 000 characters)."}, status=400)

    parent = None
    raw_parent_id = payload.get("parent_id")
    if raw_parent_id:
        parent = get_object_or_404(
            AnnouncementComment,
            id=raw_parent_id,
            announcement=announcement,
            parent__isnull=True,  # only allow replying to top-level comments
        )

    comment = AnnouncementComment.objects.create(
        announcement=announcement,
        author=request.user,
        parent=parent,
        body=body,
    )
    return JsonResponse(_serialize_comment(comment, request.user.id, include_replies=False), status=201)


# ──────────────────────────────────────────────────────────────────────────────
# Private Chat
# ──────────────────────────────────────────────────────────────────────────────

_CHAT_PAGE_SIZE = 30


def _chat_user_list(team, current_user):
    """
    Return all active team members except current_user, annotated with their
    unread count (messages sent TO current_user that are still unread).
    Ordered by most-recent conversation first, then by name.
    """
    User = get_user_model()
    member_ids = list(
        TeamMembership.objects.filter(
            team=team,
            is_active=True,
            status=TeamMembership.STATUS_APPROVED,
        ).exclude(user=current_user).values_list("user_id", flat=True)
    )

    # Subquery: latest message timestamp + body for each peer's conversation with current_user
    latest_msg_sq = (
        PrivateMessage.objects.filter(team=team)
        .filter(
            Q(sender=current_user, recipient=OuterRef("pk")) |
            Q(sender=OuterRef("pk"), recipient=current_user)
        )
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    latest_msg_body_sq = (
        PrivateMessage.objects.filter(team=team)
        .filter(
            Q(sender=current_user, recipient=OuterRef("pk")) |
            Q(sender=OuterRef("pk"), recipient=current_user)
        )
        .order_by("-created_at")
        .values("body")[:1]
    )

    users = (
        User.objects.filter(id__in=member_ids)
        .select_related("profile")
        .annotate(
            latest_msg_at=Subquery(latest_msg_sq),
            latest_msg_body=Subquery(latest_msg_body_sq),
        )
        .order_by(F("latest_msg_at").desc(nulls_last=True), "first_name", "last_name", "email")
    )

    # unread counts – messages where this user is recipient, sender is the other member
    unread_qs = (
        PrivateMessage.objects.filter(
            team=team,
            recipient=current_user,
            read_at__isnull=True,
        )
        .values("sender_id")
        .annotate(cnt=Count("id"))
    )
    unread_map = {r["sender_id"]: r["cnt"] for r in unread_qs}

    result = []
    for u in users:
        profile = getattr(u, "profile", None)
        result.append({
            "id": u.id,
            "name": u.get_full_name() or u.email,
            "initials": profile.initials if profile else (u.email[:2].upper()),
            "role": profile.role if profile else "",
            "role_label": profile.get_role_display() if profile else "",
            "unread": unread_map.get(u.id, 0),
            "last_message": (u.latest_msg_body or "")[:60],
            "last_msg_ts": int(u.latest_msg_at.timestamp() * 1000) if u.latest_msg_at else 0,
        })
    return result


@login_required
def private_chat(request):
    """Main private-chat page — shows user list and selected conversation."""
    team = _resolve_active_team(request.user)
    if team is None:
        messages.error(request, "You are not a member of any active team.")
        return redirect(reverse("communication:hub"))

    users = _chat_user_list(team, request.user)
    total_unread = sum(u["unread"] for u in users)

    context = {
        "team": team,
        "chat_users": users,
        "total_unread": total_unread,
        "me_id": request.user.id,
    }
    return render(request, "communication/private_chat.html", context)


@login_required
@require_http_methods(["GET"])
def api_chat_messages(request, peer_id):
    """
    GET /communication/chat/<peer_id>/messages/?before=<msg_id>
    Returns up to _CHAT_PAGE_SIZE messages between request.user and peer_id,
    ordered newest-first, paginated by `before` cursor (exclusive).
    Also marks all messages from peer → me as read.
    """
    team = _resolve_active_team(request.user)
    if team is None:
        return JsonResponse({"error": "No active team."}, status=403)

    User = get_user_model()
    peer = get_object_or_404(User, id=peer_id)

    # Verify peer is in the same team
    in_team = TeamMembership.objects.filter(
        team=team, user=peer, is_active=True,
        status=TeamMembership.STATUS_APPROVED,
    ).exists()
    if not in_team:
        return JsonResponse({"error": "User is not in your team."}, status=403)

    # Mark unread messages from peer as read
    PrivateMessage.objects.filter(
        team=team, sender=peer, recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())

    qs = (
        PrivateMessage.objects.filter(team=team)
        .filter(Q(sender=request.user, recipient=peer) | Q(sender=peer, recipient=request.user))
        .select_related("sender", "sender__profile")
        .order_by("-created_at", "-id")
    )

    before_id = request.GET.get("before")
    after_id = request.GET.get("after")
    if before_id:
        try:
            qs = qs.filter(id__lt=int(before_id))
        except ValueError:
            pass
    elif after_id:
        # Polling for new messages: return only those newer than after_id.
        try:
            qs = qs.filter(id__gt=int(after_id))
        except ValueError:
            pass

    msgs = list(qs[:_CHAT_PAGE_SIZE])
    has_more = len(msgs) == _CHAT_PAGE_SIZE

    # Return in chronological order for the client
    msgs.reverse()

    def _fmt(msg):
        profile = getattr(msg.sender, "profile", None)
        return {
            "id": msg.id,
            "body": msg.body,
            "sender_id": msg.sender_id,
            "is_own": msg.sender_id == request.user.id,
            "author_name": msg.sender.get_full_name() or msg.sender.email,
            "author_initials": profile.initials if profile else msg.sender.email[:2].upper(),
            "author_role": profile.role if profile else "",
            "created_label": timezone.localtime(msg.created_at).strftime("%b %d, %I:%M %p"),
        }

    return JsonResponse({
        "messages": [_fmt(m) for m in msgs],
        "has_more": has_more,
        "oldest_id": msgs[0].id if msgs else None,  # fixed: was msgs[0]["id"]
    })


@login_required
@require_http_methods(["POST"])
def api_chat_send(request, peer_id):
    """
    POST /communication/chat/<peer_id>/send/
    Body: {"body": "…"}
    Sends a private message and returns the serialised message.
    """
    team = _resolve_active_team(request.user)
    if team is None:
        return JsonResponse({"error": "No active team."}, status=403)

    User = get_user_model()
    peer = get_object_or_404(User, id=peer_id)

    in_team = TeamMembership.objects.filter(
        team=team, user=peer, is_active=True,
        status=TeamMembership.STATUS_APPROVED,
    ).exists()
    if not in_team:
        return JsonResponse({"error": "User is not in your team."}, status=403)

    if request.user.id == peer.id:
        return JsonResponse({"error": "Cannot message yourself."}, status=400)

    try:
        payload = _json.loads(request.body)
    except (_json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    body = (payload.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "Message body required."}, status=400)
    if len(body) > 4000:
        return JsonResponse({"error": "Message too long (max 4 000 characters)."}, status=400)

    msg = PrivateMessage.objects.create(
        sender=request.user,
        recipient=peer,
        team=team,
        body=body,
    )
    profile = getattr(request.user, "profile", None)
    return JsonResponse({
        "id": msg.id,
        "body": msg.body,
        "sender_id": msg.sender_id,
        "is_own": True,
        "author_name": request.user.get_full_name() or request.user.email,
        "author_initials": profile.initials if profile else request.user.email[:2].upper(),
        "author_role": profile.role if profile else "",
        "created_label": timezone.localtime(msg.created_at).strftime("%b %d, %I:%M %p"),
    }, status=201)


@login_required
@require_http_methods(["GET"])
def api_chat_unread_counts(request):
    """
    GET /communication/chat/unread/
    Returns per-sender unread counts for the current user.
    Used to refresh the sidebar badges.
    """
    team = _resolve_active_team(request.user)
    if team is None:
        return JsonResponse({"counts": {}, "previews": {}, "timestamps": {}})

    rows = (
        PrivateMessage.objects.filter(
            team=team, recipient=request.user, read_at__isnull=True
        )
        .values("sender_id")
        .annotate(cnt=Count("id"))
    )
    counts = {str(r["sender_id"]): r["cnt"] for r in rows}

    # Latest message preview + timestamp per peer (both directions)
    User = get_user_model()
    member_ids = list(
        TeamMembership.objects.filter(
            team=team, is_active=True, status=TeamMembership.STATUS_APPROVED,
        ).exclude(user=request.user).values_list("user_id", flat=True)
    )
    _pm_at_sq = (
        PrivateMessage.objects.filter(team=team)
        .filter(
            Q(sender=request.user, recipient=OuterRef("pk")) |
            Q(sender=OuterRef("pk"), recipient=request.user)
        )
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    _pm_body_sq = (
        PrivateMessage.objects.filter(team=team)
        .filter(
            Q(sender=request.user, recipient=OuterRef("pk")) |
            Q(sender=OuterRef("pk"), recipient=request.user)
        )
        .order_by("-created_at")
        .values("body")[:1]
    )
    peers = (
        User.objects.filter(id__in=member_ids)
        .annotate(latest_at=Subquery(_pm_at_sq), latest_body=Subquery(_pm_body_sq))
    )
    previews = {}
    timestamps = {}
    for p in peers:
        if p.latest_body is not None:
            previews[str(p.id)] = p.latest_body[:60]
        if p.latest_at is not None:
            timestamps[str(p.id)] = int(p.latest_at.timestamp() * 1000)

    return JsonResponse({"counts": counts, "previews": previews, "timestamps": timestamps})


@login_required
@require_http_methods(["GET"])
def api_announcements_poll(request):
    """
    GET /communication/announcements/poll/?after=<announcement_id>
    Returns announcements created after the given ID (newest first, limit 20).
    Used for client-side real-time polling on the announcements page.
    """
    team = _resolve_active_team(request.user)
    after_raw = request.GET.get("after", "").strip()
    try:
        after_id = int(after_raw)
    except (TypeError, ValueError):
        return JsonResponse({"announcements": []})

    qs = _announcement_feed_queryset(request.user, team).filter(id__gt=after_id)[:20]
    payload = _build_announcements_payload(request.user, qs)
    return JsonResponse({"announcements": payload})

