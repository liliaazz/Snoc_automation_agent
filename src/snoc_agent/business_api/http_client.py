"""Synchronous httpx implementation of the telecom business API."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from urllib.parse import quote

import httpx
from pydantic import JsonValue

from snoc_agent.business_api.interface import (
    BusinessAPIResponseError,
    BusinessAPITransportError,
)
from snoc_agent.business_api.schemas import (
    BusinessAPIEndpointPaths,
    BusinessAPIResponsePayload,
    BusinessAPIResult,
    OTPNumberChangeCommand,
    PDVOnlyCommand,
    VPNAccessCommand,
)
from snoc_agent.domain.enums import OperationAction

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 502, 503, 504})
_MAX_CONFIGURED_RETRIES = 5


class HttpBusinessAPI:
    """Call configured endpoints without weakening the workflow safety policy.

    Retries are disabled unless ``idempotency_guaranteed`` is explicitly true.
    Merely sending an idempotency header does not prove that a remote server
    honors it.  Once enabled, retries remain bounded and are limited to transient
    transport failures and selected transient HTTP statuses.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        idempotency_guaranteed: bool = False,
        endpoints: BusinessAPIEndpointPaths | None = None,
        client: httpx.Client | None = None,
        backoff_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use http:// or https://")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= max_retries <= _MAX_CONFIGURED_RETRIES:
            raise ValueError(f"max_retries must be between 0 and {_MAX_CONFIGURED_RETRIES}")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

        self.base_url = base_url
        self.token = token.strip()
        self.max_retries = max_retries
        self.idempotency_guaranteed = idempotency_guaranteed
        self.endpoints = endpoints or BusinessAPIEndpointPaths()
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self.max_response_bytes = max_response_bytes
        self._timeout = httpx.Timeout(timeout_seconds)
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self._timeout)

    def create_vpn_access(
        self,
        *,
        pdv_code: str,
        phone: str,
        idempotency_key: str,
        additional_payload: Mapping[str, JsonValue] | None = None,
    ) -> BusinessAPIResult:
        command = VPNAccessCommand(
            pdv_code=pdv_code,
            phone=phone,
            idempotency_key=idempotency_key,
            additional_payload=dict(additional_payload or {}),
        )
        payload: dict[str, JsonValue] = dict(command.additional_payload)
        payload.update({"pdv_code": command.pdv_code, "phone": command.phone})
        return self._post(
            action=OperationAction.VPN_ACCESS,
            endpoint=self.endpoints.vpn_access,
            payload=payload,
            idempotency_key=command.idempotency_key,
        )

    def update_otp(
        self,
        *,
        pdv_code: str,
        new_phone: str,
        idempotency_key: str,
    ) -> BusinessAPIResult:
        command = OTPNumberChangeCommand(
            pdv_code=pdv_code,
            new_phone=new_phone,
            idempotency_key=idempotency_key,
        )
        endpoint = self.endpoints.otp_number_change.format(
            pdv_code=quote(command.pdv_code, safe=""),
            new_phone=quote(command.new_phone, safe=""),
        )
        return self._post(
            action=OperationAction.OTP_NUMBER_CHANGE,
            endpoint=endpoint,
            payload={"pdv_code": command.pdv_code, "new_phone": command.new_phone},
            idempotency_key=command.idempotency_key,
        )

    def unlock_account(self, *, pdv_code: str, idempotency_key: str) -> BusinessAPIResult:
        command = PDVOnlyCommand(pdv_code=pdv_code, idempotency_key=idempotency_key)
        endpoint = self.endpoints.account_unblock.format(pdv_code=quote(command.pdv_code, safe=""))
        return self._post(
            action=OperationAction.ACCOUNT_UNBLOCK,
            endpoint=endpoint,
            payload={"pdv_code": command.pdv_code},
            idempotency_key=command.idempotency_key,
        )

    def reset_password(self, *, pdv_code: str, idempotency_key: str) -> BusinessAPIResult:
        command = PDVOnlyCommand(pdv_code=pdv_code, idempotency_key=idempotency_key)
        endpoint = self.endpoints.password_reset.format(pdv_code=quote(command.pdv_code, safe=""))
        return self._post(
            action=OperationAction.PASSWORD_RESET,
            endpoint=endpoint,
            payload={"pdv_code": command.pdv_code},
            idempotency_key=command.idempotency_key,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpBusinessAPI:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _post(
        self,
        *,
        action: OperationAction,
        endpoint: str,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> BusinessAPIResult:
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        allowed_attempts = self.max_retries + 1 if self.idempotency_guaranteed else 1
        for attempt in range(1, allowed_attempts + 1):
            try:
                response = self._client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                if attempt < allowed_attempts:
                    self._backoff(attempt)
                    continue
                raise BusinessAPITransportError(
                    "business API transport failed without an authoritative result",
                    endpoint=endpoint,
                    attempts=attempt,
                ) from exc

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < allowed_attempts:
                self._backoff(attempt)
                continue
            if not 200 <= response.status_code < 300:
                raise BusinessAPIResponseError(
                    "business API returned a non-success HTTP status",
                    endpoint=endpoint,
                    attempts=attempt,
                    status_code=response.status_code,
                )
            if len(response.content) > self.max_response_bytes:
                raise BusinessAPIResponseError(
                    "business API response exceeded the configured size limit",
                    endpoint=endpoint,
                    attempts=attempt,
                    status_code=response.status_code,
                )
            try:
                raw_body = response.json()
                parsed = BusinessAPIResponsePayload.model_validate(raw_body)
            except ValueError as exc:
                raise BusinessAPIResponseError(
                    "business API returned an invalid response payload",
                    endpoint=endpoint,
                    attempts=attempt,
                    status_code=response.status_code,
                ) from exc
            if not parsed.success:
                raise BusinessAPIResponseError(
                    "business API explicitly reported an unsuccessful operation",
                    endpoint=endpoint,
                    attempts=attempt,
                    status_code=response.status_code,
                )
            return BusinessAPIResult(
                action=action,
                dry_run=False,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                status_code=response.status_code,
                response_body=parsed.model_dump(mode="json", exclude_none=True),
                attempts=attempt,
            )

        raise AssertionError("bounded attempt loop completed unexpectedly")

    def _backoff(self, completed_attempt: int) -> None:
        delay = self.backoff_seconds * (2 ** (completed_attempt - 1))
        if delay:
            self._sleep(delay)
