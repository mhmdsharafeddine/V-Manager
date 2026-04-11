from http.cookies import SimpleCookie

from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser

from accounts.jwt_utils import get_user_from_token


class JWTWebSocketAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        existing_user = scope.get("user")
        if not getattr(existing_user, "is_authenticated", False):
            scope["user"] = await self._resolve_user_from_jwt_cookie(scope)
        return await self.inner(scope, receive, send)

    @database_sync_to_async
    def _resolve_user_from_jwt_cookie(self, scope):
        headers = dict(scope.get("headers") or [])
        raw_cookie_header = headers.get(b"cookie", b"")
        if not raw_cookie_header:
            return AnonymousUser()

        cookie = SimpleCookie()
        cookie.load(raw_cookie_header.decode("latin1"))

        access_cookie_name = settings.JWT_ACCESS_COOKIE_NAME
        access_token_cookie = cookie.get(access_cookie_name)
        if not access_token_cookie:
            return AnonymousUser()

        user = get_user_from_token(access_token_cookie.value, token_type="access")
        return user if user is not None else AnonymousUser()


def JWTWebSocketAuthMiddlewareStack(inner):
    return JWTWebSocketAuthMiddleware(inner)
