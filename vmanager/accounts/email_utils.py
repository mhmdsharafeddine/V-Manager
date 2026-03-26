from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import EmailVerificationCode


def send_login_2fa_code(request, user):
    EmailVerificationCode.objects.filter(
        user=user,
        purpose=EmailVerificationCode.PURPOSE_LOGIN_2FA,
        used_at__isnull=True,
    ).delete()

    _, code = EmailVerificationCode.create_code(
        user,
        EmailVerificationCode.PURPOSE_LOGIN_2FA,
    )

    message = render_to_string(
        "accounts/emails/login_2fa_code.txt",
        {
            "first_name": user.first_name or user.username,
            "code": code,
            "expiry_minutes": 10,
        },
    )
    send_mail(
        subject="Your V-Manager verification code",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def send_password_reset_code(request, user):
    EmailVerificationCode.objects.filter(
        user=user,
        purpose=EmailVerificationCode.PURPOSE_PASSWORD_RESET,
        used_at__isnull=True,
    ).delete()

    _, code = EmailVerificationCode.create_code(
        user,
        EmailVerificationCode.PURPOSE_PASSWORD_RESET,
    )

    message = render_to_string(
        "accounts/emails/password_reset_code.txt",
        {
            "first_name": user.first_name or user.username,
            "code": code,
            "expiry_minutes": settings.PASSWORD_RESET_TIMEOUT // 60,
        },
    )
    send_mail(
        subject="Your V-Manager password reset code",
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return code
