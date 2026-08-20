from __future__ import annotations

import pytest

from snoc_agent.workflow.authorizer import LDAPSenderAuthorizer, StaticSenderAuthorizer


class _AllowOneDirectoryAdapter:
    def is_authorized(self, normalized_sender: str) -> bool:
        return normalized_sender == "manager@example.com"


class _FailingDirectoryAdapter:
    def is_authorized(self, normalized_sender: str) -> bool:
        raise RuntimeError(f"directory unavailable for {normalized_sender}")


def test_static_authorizer_normalizes_display_name_and_case() -> None:
    authorizer = StaticSenderAuthorizer(["manager@example.com"])

    result = authorizer.authorize("Manager Name <MANAGER@EXAMPLE.COM>")

    assert result.authorized is True
    assert result.normalized_sender == "manager@example.com"
    assert result.reason == "sender_whitelisted"


def test_static_authorizer_denies_unknown_and_header_injection() -> None:
    authorizer = StaticSenderAuthorizer(["manager@example.com"])

    unknown = authorizer.authorize("other@example.com")
    injected = authorizer.authorize("manager@example.com\r\nBcc: attacker@example.com")

    assert unknown.authorized is False
    assert unknown.reason == "sender_not_whitelisted"
    assert injected.authorized is False
    assert injected.normalized_sender is None
    assert injected.reason == "invalid_sender_address"


def test_static_authorizer_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="invalid sender"):
        StaticSenderAuthorizer(["not-an-email"])


def test_ldap_placeholder_fails_closed_without_adapter() -> None:
    result = LDAPSenderAuthorizer().authorize("manager@example.com")

    assert result.authorized is False
    assert result.reason == "ldap_adapter_not_configured"


def test_ldap_adapter_result_is_used_when_configured() -> None:
    authorizer = LDAPSenderAuthorizer(_AllowOneDirectoryAdapter())

    allowed = authorizer.authorize("Manager <manager@example.com>")
    denied = authorizer.authorize("other@example.com")

    assert allowed.authorized is True
    assert allowed.reason == "ldap_sender_authorized"
    assert denied.authorized is False
    assert denied.reason == "ldap_sender_not_authorized"


def test_ldap_lookup_error_fails_closed() -> None:
    result = LDAPSenderAuthorizer(_FailingDirectoryAdapter()).authorize("manager@example.com")

    assert result.authorized is False
    assert result.reason == "ldap_lookup_failed"
