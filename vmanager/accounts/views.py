import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .email_utils import send_login_2fa_code, send_password_reset_code
from .forms import (
    ForgotPasswordForm,
    LoginForm,
    PasswordResetCodeForm,
    PasswordResetConfirmForm,
    RegistrationForm,
    TwoFactorCodeForm,
)
from .jwt_utils import clear_auth_cookies, get_tokens_for_user, set_auth_cookies
from .models import EmailVerificationCode

User = get_user_model()
logger = logging.getLogger(__name__)

PENDING_AUTH_USER_ID = "pending_auth_user_id"
PENDING_AUTH_REMEMBER_ME = "pending_auth_remember_me"
PENDING_PASSWORD_RESET_USER_ID = "pending_password_reset_user_id"
VERIFIED_PASSWORD_RESET_USER_ID = "verified_password_reset_user_id"

EMAIL_SEND_ERROR_MESSAGE = (
    "We could not send the verification email right now. "
    "Please check the mail server settings and try again."
)
PASSWORD_RESET_EMAIL_ERROR_MESSAGE = (
    "We could not send the reset code right now. "
    "Please check the mail server settings and try again."
)


def _is_authenticated(request):
    return getattr(request.user, "is_authenticated", False)


def _redirect_if_authenticated(request):
    if _is_authenticated(request):
        return redirect("home")
    return None


def _set_pending_2fa_session(request, *, user_id: int, remember_me: bool):
    request.session[PENDING_AUTH_USER_ID] = user_id
    request.session[PENDING_AUTH_REMEMBER_ME] = remember_me


def _clear_pending_2fa_session(request):
    request.session.pop(PENDING_AUTH_USER_ID, None)
    request.session.pop(PENDING_AUTH_REMEMBER_ME, None)


def _get_pending_2fa_user(request):
    user_id = request.session.get(PENDING_AUTH_USER_ID)
    if not user_id:
        return None
    try:
        return User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        _clear_pending_2fa_session(request)
        return None


def _email_send_failed(error: Exception) -> bool:
    return isinstance(error, (SMTPException, OSError))


def _log_email_send_error(*, context: str, recipient_email: str, error: Exception):
    logger.exception(
        "Email send failure during %s for %s: %s",
        context,
        recipient_email,
        error,
    )


def _set_pending_password_reset_session(request, *, user_id: int):
    request.session[PENDING_PASSWORD_RESET_USER_ID] = user_id


def _clear_pending_password_reset_session(request):
    request.session.pop(PENDING_PASSWORD_RESET_USER_ID, None)


def _get_pending_password_reset_user(request):
    user_id = request.session.get(PENDING_PASSWORD_RESET_USER_ID)
    if not user_id:
        return None
    try:
        return User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        _clear_pending_password_reset_session(request)
        return None


def _set_verified_password_reset_session(request, *, user_id: int):
    request.session[VERIFIED_PASSWORD_RESET_USER_ID] = user_id


def _clear_verified_password_reset_session(request):
    request.session.pop(VERIFIED_PASSWORD_RESET_USER_ID, None)


def _get_verified_password_reset_user(request):
    user_id = request.session.get(VERIFIED_PASSWORD_RESET_USER_ID)
    if not user_id:
        return None
    try:
        return User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        _clear_verified_password_reset_session(request)
        return None


@require_http_methods(["GET", "POST"])
def register_view(request):
    redirect_response = _redirect_if_authenticated(request)
    if redirect_response:
        return redirect_response

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = form.save()
        except ValidationError as exc:
            form.add_error("email", str(exc))
            user = None
        if user is None:
            return render(request, "accounts/register.html", {"form": form})
        _set_pending_2fa_session(request, user_id=user.pk, remember_me=True)
        try:
            send_login_2fa_code(request, user)
        except Exception as exc:
            _clear_pending_2fa_session(request)
            if _email_send_failed(exc):
                _log_email_send_error(
                    context="registration 2FA",
                    recipient_email=user.email,
                    error=exc,
                )
                form.add_error(None, EMAIL_SEND_ERROR_MESSAGE)
            else:
                raise
        else:
            messages.success(request, "Your account was created. We sent a verification code to your email.")
            return redirect("accounts:verify_2fa")

    return render(request, "accounts/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request):
    redirect_response = _redirect_if_authenticated(request)
    if redirect_response:
        return redirect_response

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        password = form.cleaned_data["password"]
        remember_me = request.POST.get("remember_me") == "on"
        user = authenticate(request, username=email, password=password)
        if user is None:
            form.add_error(None, "Invalid email or password.")
        else:
            _set_pending_2fa_session(request, user_id=user.pk, remember_me=remember_me)
            try:
                send_login_2fa_code(request, user)
            except Exception as exc:
                _clear_pending_2fa_session(request)
                if _email_send_failed(exc):
                    _log_email_send_error(
                        context="login 2FA",
                        recipient_email=user.email,
                        error=exc,
                    )
                    form.add_error(None, EMAIL_SEND_ERROR_MESSAGE)
                else:
                    raise
            else:
                messages.success(request, "We sent a verification code to your email.")
                return redirect("accounts:verify_2fa")

    return render(request, "accounts/login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def verify_2fa_view(request):
    redirect_response = _redirect_if_authenticated(request)
    if redirect_response:
        return redirect_response

    user = _get_pending_2fa_user(request)
    if user is None:
        messages.error(request, "Please sign in again to continue.")
        return redirect("accounts:login")

    form = TwoFactorCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"]
        verification = (
            EmailVerificationCode.objects.filter(
                user=user,
                purpose=EmailVerificationCode.PURPOSE_LOGIN_2FA,
                used_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if verification is None or not verification.is_valid(code):
            form.add_error("code", "Invalid or expired verification code.")
        else:
            verification.used_at = timezone.now()
            verification.save(update_fields=["used_at"])

            access_token, refresh_token = get_tokens_for_user(user)
            remember_me = bool(request.session.get(PENDING_AUTH_REMEMBER_ME))
            _clear_pending_2fa_session(request)
            response = redirect("home")
            set_auth_cookies(response, access_token, refresh_token, persistent=remember_me)
            messages.success(request, "Signed in successfully.")
            return response

    return render(
        request,
        "accounts/verify_2fa.html",
        {
            "form": form,
            "masked_email": user.email,
        },
    )


@require_http_methods(["POST"])
def resend_2fa_code_view(request):
    user = _get_pending_2fa_user(request)
    if user is None:
        messages.error(request, "Please sign in again to continue.")
        return redirect("accounts:login")

    try:
        send_login_2fa_code(request, user)
    except Exception as exc:
        if _email_send_failed(exc):
            _log_email_send_error(
                context="resend login 2FA",
                recipient_email=user.email,
                error=exc,
            )
            messages.error(request, EMAIL_SEND_ERROR_MESSAGE)
            return redirect("accounts:verify_2fa")
        raise
    messages.success(request, "A new verification code has been sent.")
    return redirect("accounts:verify_2fa")


@require_http_methods(["GET", "POST"])
def forgot_password_view(request):
    redirect_response = _redirect_if_authenticated(request)
    if redirect_response:
        return redirect_response

    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        _clear_verified_password_reset_session(request)
        _clear_pending_password_reset_session(request)
        email = form.cleaned_data["email"]
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            _set_pending_password_reset_session(request, user_id=user.pk)
            try:
                send_password_reset_code(request, user)
            except Exception as exc:
                _clear_pending_password_reset_session(request)
                if _email_send_failed(exc):
                    _log_email_send_error(
                        context="password reset code",
                        recipient_email=user.email,
                        error=exc,
                    )
                    form.add_error(None, PASSWORD_RESET_EMAIL_ERROR_MESSAGE)
                else:
                    raise
        if not form.non_field_errors():
            return redirect("accounts:password_reset_sent")

    return render(request, "accounts/forgot_password.html", {"form": form})


@require_GET
def password_reset_sent_view(request):
    return render(request, "accounts/password_reset_sent.html")


@require_http_methods(["GET", "POST"])
def verify_password_reset_code_view(request):
    redirect_response = _redirect_if_authenticated(request)
    if redirect_response:
        return redirect_response

    user = _get_pending_password_reset_user(request)
    if user is None:
        messages.error(request, "Please request a new reset code to continue.")
        return redirect("accounts:forgot_password")

    form = PasswordResetCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"]
        verification = (
            EmailVerificationCode.objects.filter(
                user=user,
                purpose=EmailVerificationCode.PURPOSE_PASSWORD_RESET,
                used_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if verification is None or not verification.is_valid(code):
            form.add_error("code", "Invalid or expired reset code.")
        else:
            verification.used_at = timezone.now()
            verification.save(update_fields=["used_at"])
            _clear_pending_password_reset_session(request)
            _set_verified_password_reset_session(request, user_id=user.pk)
            messages.success(request, "Code verified. You can set a new password now.")
            return redirect("accounts:password_reset_confirm")

    return render(request, "accounts/verify_reset_code.html", {"form": form, "masked_email": user.email})


@require_http_methods(["POST"])
def resend_password_reset_code_view(request):
    user = _get_pending_password_reset_user(request)
    if user is None:
        messages.error(request, "Please request a new reset code to continue.")
        return redirect("accounts:forgot_password")

    try:
        send_password_reset_code(request, user)
    except Exception as exc:
        if _email_send_failed(exc):
            _log_email_send_error(
                context="resend password reset code",
                recipient_email=user.email,
                error=exc,
            )
            messages.error(request, PASSWORD_RESET_EMAIL_ERROR_MESSAGE)
            return redirect("accounts:verify_password_reset_code")
        raise

    messages.success(request, "A new password reset code has been sent.")
    return redirect("accounts:verify_password_reset_code")


@require_http_methods(["GET", "POST"])
def password_reset_confirm_view(request):
    user = _get_verified_password_reset_user(request)

    form = PasswordResetConfirmForm(request.POST or None)

    if request.method == "POST" and user and form.is_valid():
        user.set_password(form.cleaned_data["new_password1"])
        user.save(update_fields=["password"])
        EmailVerificationCode.objects.filter(
            user=user,
            purpose=EmailVerificationCode.PURPOSE_PASSWORD_RESET,
            used_at__isnull=True,
        ).update(used_at=timezone.now())
        _clear_verified_password_reset_session(request)
        messages.success(request, "Your password has been reset successfully.")
        return redirect("accounts:password_reset_complete")

    return render(
        request,
        "accounts/password_reset_confirm.html",
        {
            "form": form,
            "reset_session_valid": bool(user),
        },
    )


@require_GET
def password_reset_complete_view(request):
    return render(request, "accounts/password_reset_complete.html")


@require_http_methods(["POST"])
def logout_view(request):
    response = redirect("home")
    clear_auth_cookies(response)
    messages.success(request, "Logged out successfully.")
    return response


@require_GET
def dashboard_view(request):
    if not _is_authenticated(request):
        messages.error(request, "Please sign in to continue.")
        return redirect("accounts:login")

    return render(request, "accounts/dashboard.html")


@require_GET
def me_view(request):
    if not _is_authenticated(request):
        return JsonResponse({"detail": "Authentication required."}, status=401)

    profile = getattr(request.user, "profile", None)
    return JsonResponse(
        {
            "id": request.user.id,
            "email": request.user.email,
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "role": getattr(profile, "role", None),
            "club_name": getattr(profile, "club_name", None),
            "child_name": getattr(profile, "child_name", None),
        }
    )
