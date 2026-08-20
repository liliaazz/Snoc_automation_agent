"""Deterministic, network-free business API used by replay and tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import quote

from pydantic import JsonValue

from snoc_agent.business_api.interface import IdempotencyConflictError
from snoc_agent.business_api.schemas import (
    BusinessAPIEndpointPaths,
    BusinessAPIResult,
    OTPNumberChangeCommand,
    PDVOnlyCommand,
    RecordedBusinessAPICall,
    VPNAccessCommand,
)
from snoc_agent.domain.enums import OperationAction


class MockBusinessAPI:
    """A fail-safe adapter that records but never performs external I/O.

    Replaying the same idempotency key and identical command returns the cached
    result without recording a second execution.  Reusing the key with different
    data fails closed.
    """

    def __init__(self, *, endpoints: BusinessAPIEndpointPaths | None = None) -> None:
        self.endpoints = endpoints or BusinessAPIEndpointPaths()
        self.calls: list[RecordedBusinessAPICall] = []
        self._results: dict[str, tuple[str, BusinessAPIResult]] = {}

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
        return self._record(
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
        return self._record(
            action=OperationAction.OTP_NUMBER_CHANGE,
            endpoint=endpoint,
            payload={"pdv_code": command.pdv_code, "new_phone": command.new_phone},
            idempotency_key=command.idempotency_key,
        )

    def unlock_account(self, *, pdv_code: str, idempotency_key: str) -> BusinessAPIResult:
        command = PDVOnlyCommand(pdv_code=pdv_code, idempotency_key=idempotency_key)
        endpoint = self.endpoints.account_unblock.format(pdv_code=quote(command.pdv_code, safe=""))
        return self._record(
            action=OperationAction.ACCOUNT_UNBLOCK,
            endpoint=endpoint,
            payload={"pdv_code": command.pdv_code},
            idempotency_key=command.idempotency_key,
        )

    def reset_password(self, *, pdv_code: str, idempotency_key: str) -> BusinessAPIResult:
        command = PDVOnlyCommand(pdv_code=pdv_code, idempotency_key=idempotency_key)
        endpoint = self.endpoints.password_reset.format(pdv_code=quote(command.pdv_code, safe=""))
        return self._record(
            action=OperationAction.PASSWORD_RESET,
            endpoint=endpoint,
            payload={"pdv_code": command.pdv_code},
            idempotency_key=command.idempotency_key,
        )

    def _record(
        self,
        *,
        action: OperationAction,
        endpoint: str,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> BusinessAPIResult:
        fingerprint = json.dumps(
            {"action": action.value, "endpoint": endpoint, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prior = self._results.get(idempotency_key)
        if prior is not None:
            prior_fingerprint, prior_result = prior
            if prior_fingerprint != fingerprint:
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different operation revision"
                )
            return prior_result

        call = RecordedBusinessAPICall(
            action=action,
            idempotency_key=idempotency_key,
            endpoint=endpoint,
            payload=payload,
        )
        result = BusinessAPIResult(
            action=action,
            dry_run=True,
            idempotency_key=idempotency_key,
            endpoint=endpoint,
            status_code=None,
            response_body={
                "success": True,
                "message": "dry-run: no external operation was performed",
                "mock": True,
            },
        )
        self.calls.append(call)
        self._results[idempotency_key] = (fingerprint, result)
        return result
