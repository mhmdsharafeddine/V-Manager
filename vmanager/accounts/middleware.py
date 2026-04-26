from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.shortcuts import redirect
from django.urls import reverse

from .jwt_utils import get_user_from_token, get_tokens_for_user, set_auth_cookies


class JWTAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", AnonymousUser())
        access_token = request.COOKIES.get(settings.JWT_ACCESS_COOKIE_NAME)
        if not getattr(user, "is_authenticated", False):
            access_token = request.COOKIES.get(settings.JWT_ACCESS_COOKIE_NAME)
            jwt_user = get_user_from_token(access_token, token_type="access")

            if jwt_user:
                request.user = jwt_user
            else:
                refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)
                jwt_user = get_user_from_token(refresh_token, token_type="refresh")
                if jwt_user:
                    request.user = jwt_user
                    request._needs_token_refresh = jwt_user

        response = self.get_response(request)

        if hasattr(request, "_needs_token_refresh"):
            persistent = request.COOKIES.get("vm_remember_me") == "1"
            new_access, new_refresh = get_tokens_for_user(request._needs_token_refresh)
            set_auth_cookies(response, new_access, new_refresh, persistent=persistent)

        return response


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", AnonymousUser())
        profile = getattr(user, "profile", None) if getattr(user, "is_authenticated", False) else None

        if profile and profile.must_change_password:
            allowed_paths = {
                reverse("accounts:initial_password_change"),
                reverse("accounts:logout"),
            }
            static_url = getattr(settings, "STATIC_URL", "/static/")
            media_url = getattr(settings, "MEDIA_URL", "/media/")

            if (
                request.path not in allowed_paths
                and not request.path.startswith(static_url)
                and not request.path.startswith(media_url)
            ):
                return redirect("accounts:initial_password_change")

        return self.get_response(request)
