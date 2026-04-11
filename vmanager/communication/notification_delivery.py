import logging

from django.contrib.auth import get_user_model

from .email_utils import send_announcement_email_notifications
from .sms_utils import send_announcement_sms_notifications
from .models import Announcement

logger = logging.getLogger(__name__)
User = get_user_model()


def dispatch_announcement_notifications(*, announcement_id, recipient_ids):
    if not recipient_ids:
        return

    announcement = Announcement.objects.filter(pk=announcement_id).first()
    if announcement is None:
        return

    recipients = list(
        User.objects.filter(id__in=recipient_ids, is_active=True)
        .select_related("profile")
        .distinct()
    )
    if not recipients:
        return

    if announcement.send_email_notification:
        send_announcement_email_notifications(announcement=announcement, recipients=recipients)

    if announcement.send_sms_notification:
        send_announcement_sms_notifications(announcement=announcement, recipients=recipients)

    if announcement.send_push_notification:
        # Push delivery hook placeholder for provider integration.
        logger.info(
            "Push notification placeholder for announcement %s to %s users",
            announcement.id,
            len(recipients),
        )
