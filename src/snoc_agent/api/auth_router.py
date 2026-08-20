"""Dashboard login and persistent user management."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from snoc_agent.api.auth import Principal, current_principal, require_admin
from snoc_agent.api.local_auth import (
    authenticate_credentials,
    create_access_token,
    hash_password,
    normalize_username,
)
from snoc_agent.datetime_utils import utc_iso
from snoc_agent.db.models import DashboardUser

router = APIRouter(prefix="/api", tags=["authentication"])
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,99}$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class AccountCreate(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=1024)
    full_name: str = ""
    email: str = ""
    role: Literal["admin", "user", "normal"] = "user"

    @field_validator("username")
    @classmethod
    def username_valid(cls, value: str) -> str:
        value = value.strip()
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("username must contain 3-100 safe characters")
        return value

    @field_validator("email")
    @classmethod
    def email_valid(cls, value: str) -> str:
        value = value.strip().casefold()
        if value and ("@" not in value or len(value) > 320):
            raise ValueError("email is invalid")
        return value


class AccountUpdate(BaseModel):
    username: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=1024)
    full_name: str | None = None
    email: str | None = None
    role: Literal["admin", "user", "normal"] | None = None

    @field_validator("username")
    @classmethod
    def username_valid(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not USERNAME_RE.fullmatch(value):
                raise ValueError("username must contain 3-100 safe characters")
        return value

    @field_validator("email")
    @classmethod
    def email_valid(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip().casefold()
            if value and ("@" not in value or len(value) > 320):
                raise ValueError("email is invalid")
        return value


def serialize(user: DashboardUser) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "username": user.username,
        "fullname": user.full_name,
        "email": user.email or "",
        "role": user.role,
        "active": user.active,
        "lastLogin": utc_iso(user.last_login_at) if user.last_login_at else None,
        "managedByEnv": False,
    }


def find_user(session: Any, username: str) -> DashboardUser:
    user = (
        session.query(DashboardUser)
        .filter(DashboardUser.normalized_username == normalize_username(username))
        .one_or_none()
    )
    if user is None:
        raise HTTPException(status_code=404, detail="account not found")
    return user


def reject_reserved(request: Request, username: str) -> None:
    if normalize_username(username) == normalize_username(
        request.app.state.settings.dashboard_admin_username
    ):
        raise HTTPException(
            status_code=409,
            detail="username is reserved by the environment admin",
        )


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not (
        settings.dashboard_admin_username
        and settings.dashboard_admin_password.get_secret_value()
        and settings.auth_jwt_secret.get_secret_value()
    ):
        raise HTTPException(status_code=503, detail="authentication is not configured")
    session = request.app.state.session_factory()
    try:
        identity = authenticate_credentials(
            session,
            settings,
            username=body.username,
            password=body.password,
        )
    finally:
        session.close()
    if identity is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    token, expires_in = create_access_token(settings, identity)
    user = {
        "name": identity["full_name"],
        "username": identity["username"],
        "email": identity["email"],
        "role": identity["role"],
        "managedByEnv": identity["managed_by_env"],
    }
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": user,
    }


@router.get("/auth/me")
async def me(principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    return {
        "name": principal.full_name or principal.username,
        "username": principal.username,
        "email": principal.email,
        "role": "admin" if principal.is_admin else "user",
        "managedByEnv": principal.managed_by_env,
    }


@router.get("/accounts")
async def list_accounts(
    request: Request,
    _admin: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    session = request.app.state.session_factory()
    try:
        users = session.query(DashboardUser).order_by(DashboardUser.username).all()
        return {"accounts": [serialize(user) for user in users]}
    finally:
        session.close()


@router.post("/accounts", status_code=201)
async def create_account(
    body: AccountCreate,
    request: Request,
    _admin: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    reject_reserved(request, body.username)
    session = request.app.state.session_factory()
    try:
        if (
            session.query(DashboardUser)
            .filter(DashboardUser.normalized_username == normalize_username(body.username))
            .first()
        ):
            raise HTTPException(status_code=409, detail="username already exists")
        if (
            body.email
            and session.query(DashboardUser)
            .filter(func.lower(DashboardUser.email) == body.email)
            .first()
        ):
            raise HTTPException(status_code=409, detail="email already exists")
        user = DashboardUser(
            username=body.username,
            normalized_username=normalize_username(body.username),
            full_name=body.full_name.strip() or body.username,
            email=body.email or None,
            password_hash=hash_password(body.password),
            role="user" if body.role == "normal" else body.role,
            active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return {"status": "created", "account": serialize(user)}
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="username or email already exists") from exc
    finally:
        session.close()


@router.put("/accounts/{username}")
async def update_account(
    username: str,
    body: AccountUpdate,
    request: Request,
    _admin: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    session = request.app.state.session_factory()
    try:
        user = find_user(session, username)
        if body.username is not None:
            reject_reserved(request, body.username)
            user.username = body.username
            user.normalized_username = normalize_username(body.username)
        if body.full_name is not None:
            user.full_name = body.full_name.strip() or user.username
        if body.email is not None:
            user.email = body.email or None
        if body.password is not None:
            user.password_hash = hash_password(body.password)
        if body.role is not None:
            user.role = "user" if body.role == "normal" else body.role
        session.commit()
        session.refresh(user)
        return {"status": "updated", "account": serialize(user)}
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="username or email already exists") from exc
    finally:
        session.close()


@router.delete("/accounts/{username}")
async def delete_account(
    username: str,
    request: Request,
    admin: Annotated[Principal, Depends(require_admin)],
) -> dict[str, str]:
    session = request.app.state.session_factory()
    try:
        user = find_user(session, username)
        if admin.subject == str(user.id):
            raise HTTPException(status_code=409, detail="you cannot delete your own account")
        session.delete(user)
        session.commit()
        return {"status": "deleted"}
    finally:
        session.close()


@router.post("/accounts/{username}/toggle")
async def toggle_account(
    username: str,
    request: Request,
    admin: Annotated[Principal, Depends(require_admin)],
) -> dict[str, Any]:
    session = request.app.state.session_factory()
    try:
        user = find_user(session, username)
        if admin.subject == str(user.id):
            raise HTTPException(status_code=409, detail="you cannot deactivate your own account")
        user.active = not user.active
        session.commit()
        return {"status": "toggled", "active": user.active}
    finally:
        session.close()
