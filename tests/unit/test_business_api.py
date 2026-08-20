from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from snoc_agent.business_api import (
    BusinessAPIResponseError,
    HttpBusinessAPI,
    IdempotencyConflictError,
    MockBusinessAPI,
)
from snoc_agent.domain.enums import OperationAction


def test_mock_is_dry_run_and_deduplicates_identical_idempotency_key() -> None:
    api = MockBusinessAPI()

    first = api.unlock_account(pdv_code="12345678", idempotency_key="operation-1:revision-1")
    replay = api.unlock_account(pdv_code="12345678", idempotency_key="operation-1:revision-1")

    assert first == replay
    assert first.dry_run is True
    assert first.action is OperationAction.ACCOUNT_UNBLOCK
    assert first.endpoint == "/unlock-account/12345678"
    assert len(api.calls) == 1


def test_mock_rejects_idempotency_key_reused_for_different_payload() -> None:
    api = MockBusinessAPI()
    api.unlock_account(pdv_code="12345678", idempotency_key="same-key")

    with pytest.raises(IdempotencyConflictError):
        api.unlock_account(pdv_code="87654321", idempotency_key="same-key")


def test_mock_validates_pdv_and_reserved_additional_fields() -> None:
    api = MockBusinessAPI()

    with pytest.raises(ValidationError):
        api.reset_password(pdv_code="123", idempotency_key="reset-1")
    with pytest.raises(ValidationError):
        api.create_vpn_access(
            pdv_code="12345678",
            phone="777888999",
            idempotency_key="vpn-1",
            additional_payload={"pdv_code": "99999999"},
        )
    with pytest.raises(ValidationError):
        api.unlock_account(
            pdv_code="12345678",
            idempotency_key="unsafe header\r\nX-Forged: yes",
        )


def test_http_adapter_uses_mapping_auth_and_idempotency_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"success": True, "reference": "remote-42"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = HttpBusinessAPI(
        base_url="https://business.example.test/",
        token="secret-token",
        max_retries=0,
        client=client,
    )

    result = api.update_otp(
        pdv_code="12345678",
        new_phone="777888999",
        idempotency_key="operation-2:revision-3",
    )

    assert result.success is True
    assert result.dry_run is False
    assert result.attempts == 1
    assert str(requests[0].url) == "https://business.example.test/update-otp/12345678/777888999"
    assert requests[0].headers["Idempotency-Key"] == "operation-2:revision-3"
    assert requests[0].headers["Authorization"] == "Bearer secret-token"
    assert json.loads(requests[0].content) == {
        "pdv_code": "12345678",
        "new_phone": "777888999",
    }
    client.close()


def test_http_adapter_retries_transient_status_only_with_guaranteed_idempotency() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"success": False})
        return httpx.Response(200, json={"success": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = HttpBusinessAPI(
        base_url="https://business.example.test",
        max_retries=2,
        idempotency_guaranteed=True,
        client=client,
    )

    result = api.reset_password(pdv_code="12345678", idempotency_key="reset-retry")

    assert attempts == 2
    assert result.attempts == 2
    client.close()


def test_http_adapter_does_not_retry_when_remote_idempotency_is_not_guaranteed() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"success": False})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = HttpBusinessAPI(
        base_url="https://business.example.test",
        max_retries=5,
        idempotency_guaranteed=False,
        client=client,
    )

    with pytest.raises(BusinessAPIResponseError) as raised:
        api.unlock_account(pdv_code="12345678", idempotency_key="no-unsafe-retry")

    assert attempts == 1
    assert raised.value.attempts == 1
    client.close()


@pytest.mark.parametrize("status_code", [400, 401, 404, 409, 422, 500])
def test_http_adapter_fails_closed_without_retry_for_permanent_statuses(
    status_code: int,
) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, json={"success": False})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = HttpBusinessAPI(
        base_url="https://business.example.test",
        max_retries=2,
        idempotency_guaranteed=True,
        client=client,
    )

    with pytest.raises(BusinessAPIResponseError) as raised:
        api.unlock_account(pdv_code="12345678", idempotency_key=f"status-{status_code}")

    assert attempts == 1
    assert raised.value.status_code == status_code
    client.close()


@pytest.mark.parametrize("status_code", [429, 502])
def test_http_adapter_retries_bounded_transient_statuses(status_code: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, json={"success": False})
        return httpx.Response(200, json={"success": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = HttpBusinessAPI(
        base_url="https://business.example.test",
        max_retries=1,
        idempotency_guaranteed=True,
        client=client,
    )

    result = api.unlock_account(
        pdv_code="12345678",
        idempotency_key=f"transient-{status_code}",
    )

    assert attempts == 2
    assert result.attempts == 2
    client.close()


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectError,
        httpx.ReadTimeout,
    ],
)
def test_http_adapter_retries_bounded_transport_failures(
    error_type: type[httpx.HTTPError],
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error_type("transient transport failure", request=request)
        return httpx.Response(200, json={"success": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = HttpBusinessAPI(
        base_url="https://business.example.test",
        max_retries=1,
        idempotency_guaranteed=True,
        client=client,
    )

    result = api.unlock_account(
        pdv_code="12345678",
        idempotency_key=f"transport-{error_type.__name__}",
    )

    assert attempts == 2
    assert result.attempts == 2
    client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json=[{"success": True}]),
        httpx.Response(200, json={"message": "missing explicit success"}),
        httpx.Response(200, json={"success": "yes"}),
        httpx.Response(200, json={"success": False}),
    ],
)
def test_http_adapter_rejects_untrusted_or_unsuccessful_response(
    response: httpx.Response,
) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: response))
    api = HttpBusinessAPI(base_url="https://business.example.test", client=client)

    with pytest.raises(BusinessAPIResponseError):
        api.unlock_account(pdv_code="12345678", idempotency_key="unsafe-response")

    client.close()


def test_http_adapter_caps_configured_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        HttpBusinessAPI(base_url="https://business.example.test", max_retries=6)
