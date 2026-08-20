"""Local dashboard credentials and signed session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy.orm import Session

from snoc_agent.config import Settings
from snoc_agent.datetime_utils import utc_now

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000
TOKEN_ALGORITHM = "HS256"
TOKEN_ISSUER = "snoc-dashboard"
TOKEN_AUDIENCE = "snoc-dashboard"


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(raw_iterations)
        if iterations < 1 or iterations > 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode())
        expected = base64.urlsafe_b64decode(raw_digest.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _consume_invalid_password(password: str) -> None:
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        b"snoc-dashboard-invalid-user",
        PASSWORD_ITERATIONS,
    )
    hmac.compare_digest(candidate, bytes(len(candidate)))


def authenticate_credentials(
    session: Session,
    settings: Settings,
    *,
    username: str,
    password: str,
) -> dict[str, Any] | None:
    from snoc_agent.db.models import DashboardUser

    normalized = normalize_username(username)
    env_username = normalize_username(settings.dashboard_admin_username)
    env_password = settings.dashboard_admin_password.get_secret_value()
    if env_username and hmac.compare_digest(normalized, env_username):
        if env_password and hmac.compare_digest(password, env_password):
            return {
                "subject": "bootstrap-admin",
                "username": settings.dashboard_admin_username.strip(),
                "full_name": "SNOC Administrator",
                "email": "",
                "role": "admin",
                "managed_by_env": True,
            }
        _consume_invalid_password(password)
        return None

    user = (
        session.query(DashboardUser)
        .filter(DashboardUser.normalized_username == normalized)
        .one_or_none()
    )
    if user is None:
        _consume_invalid_password(password)
        return None
    if not user.active or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = utc_now()
    session.commit()
    return {
        "subject": str(user.id),
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email or "",
        "role": user.role,
        "managed_by_env": False,
    }


def create_access_token(settings: Settings, identity: dict[str, Any]) -> tuple[str, int]:
    now = utc_now()
    ttl_seconds = settings.auth_token_ttl_minutes * 60
    claims = {
        "sub": identity["subject"],
        "username": identity["username"],
        "name": identity["full_name"],
        "email": identity["email"],
        "role": identity["role"],
        "roles": ["SNOC_ADMIN"] if identity["role"] == "admin" else ["SNOC_USER"],
        "managed_by_env": bool(identity["managed_by_env"]),
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
    }
    secret = settings.auth_jwt_secret.get_secret_value()
    if not secret:
        raise ValueError("AUTH_JWT_SECRET is not configured")
    return jwt.encode(claims, secret, algorithm=TOKEN_ALGORITHM), ttl_seconds


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.auth_jwt_secret.get_secret_value(),
        algorithms=[TOKEN_ALGORITHM],
        issuer=TOKEN_ISSUER,
        audience=TOKEN_AUDIENCE,
        options={"require": ["exp", "iat", "sub", "username", "role"]},
    )
