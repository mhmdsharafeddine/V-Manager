"""
Management command: send_digest_emails
---------------------------------------
Sends queued announcement emails from DigestEmailQueue whose send_after
timestamp has passed but haven't been sent yet.

Run manually or via a cron job at 10 PM daily:
    python manage.py send_digest_emails

Typical crontab entry (runs every day at 22:00):
    0 22 * * * /path/to/.venv/bin/python /path/to/vmanager/manage.py send_digest_emails

The command groups pending items by user and sends ONE digest email per user
with all their queued announcements, rather than one email per announcement.
"""

from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from communication.models import DigestEmailQueue


class Command(BaseCommand):
    help = "Send queued announcement digest / held emails to recipients."

    def handle(self, *args, **options):
        now = timezone.now()

        pending = list(
            DigestEmailQueue.objects.filter(
                sent_at__isnull=True,
                send_after__lte=now,
            )
            .select_related("user", "announcement", "announcement__created_by")
            .order_by("user_id", "send_after")
        )

        if not pending:
            self.stdout.write("No queued emails to send.")
            return

        # Group by user
        by_user = defaultdict(list)
        for item in pending:
            by_user[item.user_id].append(item)

        sent_ids = []
        errors = 0

        for user_id, items in by_user.items():
            user = items[0].user
            if not user.email or not user.is_active:
                sent_ids.extend(i.id for i in items)  # mark as "sent" to clear queue
                continue

            announcements = [i.announcement for i in items]

            message = render_to_string(
                "communication/emails/digest_notification.txt",
                {
                    "first_name": user.first_name or user.username,
                    "announcements": [
                        {
                            "title": a.title,
                            "body": a.body,
                            "priority": a.get_priority_display(),
                            "sender_name": a.created_by.get_full_name() or a.created_by.email,
                        }
                        for a in announcements
                    ],
                    "count": len(announcements),
                },
            )

            try:
                send_mail(
                    subject=f"V-Manager: {len(announcements)} announcement(s) for you",
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                sent_ids.extend(i.id for i in items)
            except Exception as exc:
                self.stderr.write(f"Failed to send digest to {user.email}: {exc}")
                errors += 1

        if sent_ids:
            DigestEmailQueue.objects.filter(id__in=sent_ids).update(sent_at=now)

        self.stdout.write(
            f"Digest run complete: {len(sent_ids)} queued email(s) sent, {errors} error(s)."
        )
