import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.contrib.auth import authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .email_utils import send_login_2fa_code, send_password_reset_code
from .forms import (
    AccountSettingsForm,
    ForgotPasswordForm,
    LinkedPlayerRegistrationFormSet,
    LoginForm,
    PasswordResetCodeForm,
    PasswordResetConfirmForm,
    RegistrationForm,
    TwoFactorCodeForm,
)
from .jwt_utils import clear_auth_cookies, get_tokens_for_user, set_auth_cookies
from .models import AccountProfile, EmailVerificationCode
from team_management.models import TeamMembership

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


def _post_login_redirect_name(user):
    membership = getattr(user, "team_membership", None)
    if membership and membership.status in {
        TeamMembership.STATUS_PENDING,
        TeamMembership.STATUS_REJECTED,
    }:
        return "accounts:membership_status"
    return "home"


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


def _account_settings_context(request, form):
    profile = getattr(request.user, "profile", None)
    membership = getattr(request.user, "team_membership", None)
    linked_children = profile.linked_children_profiles() if profile else []
    return {
        "form": form,
        "profile": profile,
        "membership": membership,
        "team_name": getattr(getattr(membership, "team", None), "name", ""),
        "linked_children": linked_children,
    }


@require_http_methods(["GET", "POST"])
def register_view(request):
    redirect_response = _redirect_if_authenticated(request)
    if redirect_response:
        return redirect_response

    form = RegistrationForm(request.POST or None)
    linked_child_formset = LinkedPlayerRegistrationFormSet(request.POST or None, prefix="children")

    if request.method == "POST":
        form_valid = form.is_valid()
        signup_type = form.cleaned_data.get("signup_type") if form_valid else (request.POST.get("signup_type") or "").strip()
        requested_role = form.cleaned_data.get("requested_role") if form_valid else (request.POST.get("requested_role") or "").strip()
        needs_child_formset = signup_type == RegistrationForm.SIGNUP_MEMBER and requested_role == AccountProfile.ROLE_PARENT

        child_formset_valid = True
        linked_children = []
        if needs_child_formset:
            child_formset_valid = linked_child_formset.is_valid()
            linked_children = [
                child_form.cleaned_data
                for child_form in linked_child_formset.forms
                if getattr(child_form, "cleaned_data", None) and not child_form.cleaned_data.get("DELETE")
            ]
            if form_valid:
                parent_email = form.cleaned_data.get("email")
                child_emails = [child_data.get("email") for child_data in linked_children if child_data.get("email")]
                if parent_email and parent_email in child_emails:
                    child_formset_valid = False
                    for child_form in linked_child_formset.forms:
                        if getattr(child_form, "cleaned_data", None) and child_form.cleaned_data.get("email") == parent_email:
                            child_form.add_error("email", "Child email must be different from the parent email.")

        if form_valid and child_formset_valid:
            try:
                form.save(linked_children=linked_children)
            except ValidationError as exc:
                form.add_error("email", str(exc))
            else:
                if needs_child_formset:
                    child_count = len(linked_children)
                    child_label = "child accounts" if child_count != 1 else "child account"
                    messages.success(
                        request,
                        f"Parent and {child_count} linked {child_label} were created successfully. All requests are now waiting for manager approval. Please sign in.",
                    )
                elif signup_type == RegistrationForm.SIGNUP_MEMBER:
                    messages.success(request, "Your account request was submitted successfully. Please sign in.")
                else:
                    messages.success(request, "Your account was created successfully. Please sign in.")
                return redirect("accounts:login")

    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "linked_child_formset": linked_child_formset,
        },
    )


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

from django.contrib.auth import login as django_login
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

            django_login(request, user)  # ← add this
            response = redirect(_post_login_redirect_name(user))
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


@require_GET
def membership_status_view(request):
    if not _is_authenticated(request):
        messages.error(request, "Please sign in to continue.")
        return redirect("accounts:login")

    membership = getattr(request.user, "team_membership", None)
    if membership is None:
        return redirect("home")

    if membership.status == TeamMembership.STATUS_APPROVED and membership.is_active:
        messages.success(request, "Your team access has been approved.")
        return redirect("home")

    return render(
        request,
        "accounts/membership_status.html",
        {
            "membership": membership,
            "team_name": membership.team.name,
            "requested_role": dict(AccountProfile.ROLE_CHOICES).get(
                membership.requested_role,
                membership.requested_role.title() if membership.requested_role else "Member",
            ),
        },
    )


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


from django.contrib.auth import logout as django_logout

@require_http_methods(["POST"])
def logout_view(request):
    django_logout(request)  # clears the session
    response = redirect("home")
    clear_auth_cookies(response)
    messages.success(request, "Logged out successfully.")
    return response


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    form = AccountSettingsForm(request.user, request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        changed_password = bool(form.cleaned_data.get("new_password1"))
        user = form.save()
        if changed_password:
            update_session_auth_hash(request, user)
        messages.success(request, "Account settings updated successfully.")
        return redirect("accounts:settings")

    return render(
        request,
        "accounts/settings.html",
        _account_settings_context(request, form),
    )


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
