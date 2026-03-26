from django.conf import settings
from django.contrib.auth.models import AnonymousUser

from .jwt_utils import get_user_from_token


class JWTAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", AnonymousUser())
        if not getattr(user, "is_authenticated", False):
            token = request.COOKIES.get(settings.JWT_ACCESS_COOKIE_NAME)
            jwt_user = get_user_from_token(token, token_type="access")
            if jwt_user:
                request.user = jwt_user

        return self.get_response(request)
