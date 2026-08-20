"""Business-operation adapters and their validated public contract."""

from snoc_agent.business_api.http_client import HttpBusinessAPI
from snoc_agent.business_api.interface import (
    BusinessAPI,
    BusinessAPIError,
    BusinessAPIResponseError,
    BusinessAPITransportError,
    IdempotencyConflictError,
)
from snoc_agent.business_api.mock_client import MockBusinessAPI
from snoc_agent.business_api.schemas import (
    BusinessAPIEndpointPaths,
    BusinessAPIResponsePayload,
    BusinessAPIResult,
    OTPNumberChangeCommand,
    PDVOnlyCommand,
    RecordedBusinessAPICall,
    VPNAccessCommand,
)

__all__ = [
    "BusinessAPI",
    "BusinessAPIEndpointPaths",
    "BusinessAPIError",
    "BusinessAPIResponseError",
    "BusinessAPIResponsePayload",
    "BusinessAPIResult",
    "BusinessAPITransportError",
    "HttpBusinessAPI",
    "IdempotencyConflictError",
    "MockBusinessAPI",
    "OTPNumberChangeCommand",
    "PDVOnlyCommand",
    "RecordedBusinessAPICall",
    "VPNAccessCommand",
]
