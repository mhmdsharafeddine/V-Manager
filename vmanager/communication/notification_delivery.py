import logging
from datetime import timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import NotificationPreferences
from scheduling.notifications import (
    _announcement_meets_min_priority,
    _in_quiet_hours,
)

from .email_utils import send_announcement_email_notifications
from .models import Announcement, DigestEmailQueue

logger = logging.getLogger(__name__)
User = get_user_model()


def _next_10pm_utc(now):
    """Return the next 22:00 in local time expressed as UTC."""
    local_now = timezone.localtime(now)
    target = local_now.replace(hour=22, minute=0, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return target.astimezone(dt_timezone.utc)


def _quiet_hours_end_utc(prefs, now):
    """Return the next moment quiet hours end, expressed as UTC."""
    local_now = timezone.localtime(now)
    qe = prefs.quiet_end
    target = local_now.replace(hour=qe.hour, minute=qe.minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    return target.astimezone(dt_timezone.utc)


def dispatch_announcement_notifications(*, announcement_id, recipient_ids):
    if not recipient_ids:
        return

    announcement = Announcement.objects.filter(pk=announcement_id).select_related("created_by").first()
    if announcement is None:
        return

    recipients = list(
        User.objects.filter(id__in=recipient_ids, is_active=True)
        .select_related("profile")
        .distinct()
    )
    if not recipients:
        return

    # Load all preferences in one query.
    prefs_map = {
        p.user_id: p
        for p in NotificationPreferences.objects.filter(
            user_id__in=[r.id for r in recipients]
        )
    }
    now = timezone.now()

    immediate_recipients = []
    digest_items = []    # (user, reason, send_after)

    for recipient in recipients:
        prefs = prefs_map.get(recipient.id)

        # ── Email kill-switch ──────────────────────────────────────────────
        if prefs is not None:
            if not prefs.email_enabled or not prefs.announcements_enabled:
                continue
            if not _announcement_meets_min_priority(
                announcement.priority, prefs.announcement_min_priority
            ):
                continue

        # ── Quiet hours ────────────────────────────────────────────────────
        in_quiet = prefs is not None and _in_quiet_hours(prefs, now)
        if in_quiet:
            if prefs.quiet_skip_entirely:
                # Hard drop — no email at all.
                continue
            else:
                # Hold until quiet hours end.
                send_after = _quiet_hours_end_utc(prefs, now)
                digest_items.append((recipient, DigestEmailQueue.REASON_QUIET_HOLD, send_after))
                continue

        # ── Daily digest ───────────────────────────────────────────────────
        if prefs is not None and prefs.announcement_digest == NotificationPreferences.DIGEST_DAILY:
            send_after = _next_10pm_utc(now)
            digest_items.append((recipient, DigestEmailQueue.REASON_DIGEST, send_after))
            continue

        immediate_recipients.append(recipient)

    # ── Send immediately ───────────────────────────────────────────────────
    if announcement.send_email_notification and immediate_recipients:
        send_announcement_email_notifications(
            announcement=announcement, recipients=immediate_recipients
        )

    # ── Queue digest / held emails ─────────────────────────────────────────
    if announcement.send_email_notification and digest_items:
        for (user, reason, send_after) in digest_items:
            DigestEmailQueue.objects.get_or_create(
                user=user,
                announcement=announcement,
                defaults={
                    "reason": reason,
                    "send_after": send_after,
                },
            )

    if announcement.send_push_notification:
        logger.info(
            "Push notification placeholder for announcement %s to %s users",
            announcement.id,
            len(recipients),
        )

