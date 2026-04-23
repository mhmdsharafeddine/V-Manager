from django.conf import settings
from django.contrib.auth.models import AnonymousUser

from .jwt_utils import get_user_from_token, get_tokens_for_user, set_auth_cookies


class JWTAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", AnonymousUser())
        access_token = request.COOKIES.get(settings.JWT_ACCESS_COOKIE_NAME)
        refresh_token = request.COOKIES.get(settings.JWT_REFRESH_COOKIE_NAME)
        print(f"ACCESS COOKIE: {bool(access_token)}")
        print(f"REFRESH COOKIE: {bool(refresh_token)}")
        print(f"ALL COOKIES: {list(request.COOKIES.keys())}")
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