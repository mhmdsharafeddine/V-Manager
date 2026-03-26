import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _get_secret() -> bytes:
    return settings.SECRET_KEY.encode("utf-8")


def encode_jwt(payload: dict) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(_get_secret(), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def decode_jwt(token: str, *, token_type: str | None = None) -> dict | None:
    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    header_part, payload_part, signature_part = parts
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    expected_signature = hmac.new(_get_secret(), signing_input, hashlib.sha256).digest()

    try:
        actual_signature = _b64url_decode(signature_part)
    except (ValueError, TypeError):
        return None

    if not hmac.compare_digest(expected_signature, actual_signature):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_part))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None

    now = int(time.time())
    if payload.get("exp", 0) < now:
        return None

    if token_type and payload.get("type") != token_type:
        return None

    return payload


def _token_payload(user, *, token_type: str, lifetime: timedelta) -> dict:
    now = int(time.time())
    return {
        "sub": str(user.pk),
        "email": user.email,
        "type": token_type,
        "iat": now,
        "exp": now + int(lifetime.total_seconds()),
        "jti": uuid.uuid4().hex,
    }


def create_access_token(user) -> str:
    return encode_jwt(
        _token_payload(
            user,
            token_type="access",
            lifetime=getattr(settings, "JWT_ACCESS_TOKEN_LIFETIME", timedelta(minutes=15)),
        )
    )


def create_refresh_token(user) -> str:
    return encode_jwt(
        _token_payload(
            user,
            token_type="refresh",
            lifetime=getattr(settings, "JWT_REFRESH_TOKEN_LIFETIME", timedelta(days=7)),
        )
    )


def get_user_from_token(token: str, *, token_type: str = "access"):
    payload = decode_jwt(token, token_type=token_type)
    if not payload:
        return None

    try:
        return User.objects.get(pk=payload["sub"], is_active=True)
    except (User.DoesNotExist, KeyError, ValueError, TypeError):
        return None


def get_tokens_for_user(user) -> tuple[str, str]:
    return create_access_token(user), create_refresh_token(user)


def set_auth_cookies(response, access_token: str, refresh_token: str, *, persistent: bool = True):
    secure_flag = not settings.DEBUG
    access_cookie_name = settings.JWT_ACCESS_COOKIE_NAME
    refresh_cookie_name = settings.JWT_REFRESH_COOKIE_NAME
    remember_cookie_name = "vm_remember_me"

    access_max_age = int(settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds())
    refresh_max_age = int(settings.JWT_REFRESH_TOKEN_LIFETIME.total_seconds())

    access_cookie_kwargs = {
        "httponly": True,
        "secure": secure_flag,
        "samesite": "Lax",
        "path": "/",
    }
    refresh_cookie_kwargs = {
        "httponly": True,
        "secure": secure_flag,
        "samesite": "Lax",
        "path": "/",
    }

    if persistent:
        access_cookie_kwargs["max_age"] = access_max_age
        refresh_cookie_kwargs["max_age"] = refresh_max_age

    response.set_cookie(
        access_cookie_name,
        access_token,
        **access_cookie_kwargs,
    )
    response.set_cookie(
        refresh_cookie_name,
        refresh_token,
        **refresh_cookie_kwargs,
    )

    if persistent:
        response.set_cookie(
            remember_cookie_name,
            "1",
            max_age=refresh_max_age,
            secure=secure_flag,
            samesite="Lax",
            path="/",
        )
    else:
        response.delete_cookie(remember_cookie_name, path="/", samesite="Lax")

    return response


def clear_auth_cookies(response):
    response.delete_cookie(settings.JWT_ACCESS_COOKIE_NAME, path="/", samesite="Lax")
    response.delete_cookie(settings.JWT_REFRESH_COOKIE_NAME, path="/", samesite="Lax")
    response.delete_cookie("vm_remember_me", path="/", samesite="Lax")
    return response
