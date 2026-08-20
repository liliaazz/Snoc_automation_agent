"""Authentication dependencies for locally signed dashboard sessions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from snoc_agent.api.local_auth import decode_access_token, normalize_username

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    username: str = ""
    full_name: str = ""
    email: str = ""
    roles: frozenset[str] = field(default_factory=frozenset)
    authenticated: bool = False
    managed_by_env: bool = False

    @property
    def is_admin(self) -> bool:
        return bool(self.roles.intersection({"ADMIN", "SNOC_ADMIN"}))

    @property
    def can_view_sensitive_details(self) -> bool:
        return bool(self.roles.intersection({"ADMIN", "SNOC_ADMIN", "AUDITOR"}))


async def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
) -> Principal:
    settings = request.app.state.settings

    if not settings.auth_jwt_secret.get_secret_value():
        if settings.dry_run:
            return Principal(subject="development-readonly", authenticated=False)
        raise HTTPException(status_code=503, detail="authentication is not configured")

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="bearer token required")

    try:
        claims = decode_access_token(settings, credentials.credentials)
        subject = str(claims["sub"])
        token_username = str(claims["username"])
        if subject == "bootstrap-admin":
            if normalize_username(token_username) != normalize_username(
                settings.dashboard_admin_username
            ):
                raise ValueError("bootstrap administrator changed")
            return Principal(
                subject=subject,
                username=settings.dashboard_admin_username,
                full_name="SNOC Administrator",
                roles=frozenset({"SNOC_ADMIN"}),
                authenticated=True,
                managed_by_env=True,
            )

        from snoc_agent.db.models import DashboardUser

        session = request.app.state.session_factory()
        try:
            user = session.get(DashboardUser, uuid.UUID(subject))
            if user is None or not user.active:
                raise ValueError("dashboard user is unavailable")
            if normalize_username(token_username) != user.normalized_username:
                raise ValueError("dashboard username changed")
            roles = frozenset({"SNOC_ADMIN" if user.role == "admin" else "SNOC_USER"})
            return Principal(
                subject=subject,
                username=user.username,
                full_name=user.full_name,
                email=user.email or "",
                roles=roles,
                authenticated=True,
            )
        finally:
            session.close()
    except Exception as exc:
        logger.warning("dashboard session validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc


async def require_admin(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    """Dependency that requires admin role."""
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return principal
