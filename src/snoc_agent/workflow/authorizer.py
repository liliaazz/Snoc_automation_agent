"""Deterministic sender authorization, kept outside the model boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Protocol, runtime_checkable

_MAILBOX_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.)+[A-Z]{2,63}$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    authorized: bool
    normalized_sender: str | None
    reason: str
    source: str


@runtime_checkable
class SenderAuthorizer(Protocol):
    def authorize(self, sender: str) -> AuthorizationResult: ...


@runtime_checkable
class LDAPAuthorizationAdapter(Protocol):
    """Small seam for a future LDAP/Active Directory implementation."""

    def is_authorized(self, normalized_sender: str) -> bool: ...


def normalize_sender(sender: str) -> str | None:
    """Return one canonical mailbox or ``None`` for malformed/header-injection input."""

    if not isinstance(sender, str) or not sender.strip():
        return None
    if any(character in sender for character in ("\r", "\n", "\x00")):
        return None
    parsed = [address for _display_name, address in getaddresses([sender]) if address]
    if len(parsed) != 1:
        return None
    normalized = parsed[0].strip().casefold()
    if not _MAILBOX_PATTERN.fullmatch(normalized):
        return None
    return normalized


class StaticSenderAuthorizer:
    """Case-insensitive local whitelist suitable for the MVP and tests."""

    def __init__(
        self, authorized_senders: set[str] | frozenset[str] | list[str] | tuple[str, ...]
    ) -> None:
        normalized: set[str] = set()
        for configured_sender in authorized_senders:
            address = normalize_sender(configured_sender)
            if address is None:
                raise ValueError(
                    f"invalid sender address in authorization configuration: {configured_sender!r}"
                )
            normalized.add(address)
        self.authorized_senders = frozenset(normalized)

    def authorize(self, sender: str) -> AuthorizationResult:
        normalized = normalize_sender(sender)
        if normalized is None:
            return AuthorizationResult(
                authorized=False,
                normalized_sender=None,
                reason="invalid_sender_address",
                source="static",
            )
        if normalized in self.authorized_senders:
            return AuthorizationResult(
                authorized=True,
                normalized_sender=normalized,
                reason="sender_whitelisted",
                source="static",
            )
        return AuthorizationResult(
            authorized=False,
            normalized_sender=normalized,
            reason="sender_not_whitelisted",
            source="static",
        )


class LDAPSenderAuthorizer:
    """Fail-closed wrapper around an optional LDAP/AD lookup adapter.

    No LDAP library or credentials are assumed in the MVP.  Deployments can
    provide an adapter implementing :class:`LDAPAuthorizationAdapter`; until
    then every lookup is denied with an auditable reason.
    """

    def __init__(self, adapter: LDAPAuthorizationAdapter | None = None) -> None:
        self.adapter = adapter

    def authorize(self, sender: str) -> AuthorizationResult:
        normalized = normalize_sender(sender)
        if normalized is None:
            return AuthorizationResult(
                authorized=False,
                normalized_sender=None,
                reason="invalid_sender_address",
                source="ldap",
            )
        if self.adapter is None:
            return AuthorizationResult(
                authorized=False,
                normalized_sender=normalized,
                reason="ldap_adapter_not_configured",
                source="ldap",
            )
        try:
            authorized = bool(self.adapter.is_authorized(normalized))
        except Exception:
            return AuthorizationResult(
                authorized=False,
                normalized_sender=normalized,
                reason="ldap_lookup_failed",
                source="ldap",
            )
        return AuthorizationResult(
            authorized=authorized,
            normalized_sender=normalized,
            reason="ldap_sender_authorized" if authorized else "ldap_sender_not_authorized",
            source="ldap",
        )


__all__ = [
    "AuthorizationResult",
    "LDAPAuthorizationAdapter",
    "LDAPSenderAuthorizer",
    "SenderAuthorizer",
    "StaticSenderAuthorizer",
    "normalize_sender",
]
