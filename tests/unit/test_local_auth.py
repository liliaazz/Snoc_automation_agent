from __future__ import annotations

import pytest
from jwt import InvalidAudienceError, InvalidSignatureError

from snoc_agent.api.local_auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    normalize_username,
    verify_password,
)
from snoc_agent.config import Settings


def _settings(secret: str = "unit-test-dashboard-signing-secret-at-least-32-chars") -> Settings:
    return Settings(
        dry_run=True,
        auth_jwt_secret=secret,
        auth_token_ttl_minutes=15,
    )


def test_password_hash_is_salted_and_verifies_without_plaintext() -> None:
    first = hash_password("A-safe-local-password")
    second = hash_password("A-safe-local-password")

    assert first != second
    assert "A-safe-local-password" not in first
    assert verify_password("A-safe-local-password", first)
    assert not verify_password("wrong-password", first)
    assert not verify_password("A-safe-local-password", "not-a-valid-hash")


def test_local_token_round_trip_and_signature_validation() -> None:
    settings = _settings()
    identity = {
        "subject": "bootstrap-admin",
        "username": "snoc-admin",
        "full_name": "SNOC Administrator",
        "email": "",
        "role": "admin",
        "managed_by_env": True,
    }

    token, ttl_seconds = create_access_token(settings, identity)
    claims = decode_access_token(settings, token)

    assert ttl_seconds == 900
    assert claims["sub"] == "bootstrap-admin"
    assert claims["roles"] == ["SNOC_ADMIN"]
    with pytest.raises(InvalidSignatureError):
        decode_access_token(
            _settings("different-unit-test-signing-secret-at-least-32-chars"), token
        )


def test_token_rejects_wrong_audience() -> None:
    import jwt

    settings = _settings()
    token = jwt.encode(
        {
            "sub": "bootstrap-admin",
            "username": "snoc-admin",
            "role": "admin",
            "iat": 1,
            "exp": 4_102_444_800,
            "iss": "snoc-dashboard",
            "aud": "wrong-dashboard",
        },
        settings.auth_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )

    with pytest.raises(InvalidAudienceError):
        decode_access_token(settings, token)


def test_username_normalization_is_case_insensitive() -> None:
    assert normalize_username("  SNOC.Admin ") == "snoc.admin"
