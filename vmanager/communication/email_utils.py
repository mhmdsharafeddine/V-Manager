from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string


def send_announcement_email_notifications(*, announcement, recipients):
    for recipient in recipients:
        if not recipient.email:
            continue

        message = render_to_string(
            "communication/emails/announcement_notification.txt",
            {
                "first_name": recipient.first_name or recipient.username,
                "announcement_title": announcement.title,
                "announcement_body": announcement.body,
                "priority": announcement.get_priority_display(),
                "audience": announcement.get_audience_display(),
                "sender_name": announcement.created_by.get_full_name() or announcement.created_by.email,
            },
        )

        send_mail(
            subject=f"New V-Manager announcement: {announcement.title}",
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient.email],
            fail_silently=False,
        )
