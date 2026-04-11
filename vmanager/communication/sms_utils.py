import logging

from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_announcement_sms_notifications(*, announcement, recipients):
    for recipient in recipients:
        profile = getattr(recipient, "profile", None)
        phone_number = getattr(profile, "phone_number", "") if profile else ""
        if not phone_number:
            continue

        message = render_to_string(
            "communication/sms/announcement_notification.txt",
            {
                "announcement_title": announcement.title,
                "priority": announcement.get_priority_display(),
                "sender_name": announcement.created_by.get_full_name() or announcement.created_by.email,
            },
        ).strip()

        # SMS delivery hook placeholder for provider integration.
        logger.info("SMS notification placeholder to %s: %s", phone_number, message)
