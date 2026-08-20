"""Typed configuration loaded from environment variables or an optional .env file."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from snoc_agent.ai.provider import LLMProvider, VLLMDeploymentName
from snoc_agent.ai.vllm_deployments import VLLMDeployment


class Settings(BaseSettings):
    """Application settings.

    Secrets are represented with ``SecretStr`` so accidental repr/logging does not expose them.
    Empty endpoints select local fake/dry-run adapters in CLI replay mode.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_environment: Literal["development", "production"] = "development"
    database_url: str = "sqlite:///./snoc_agent.db"
    workflow_engine: Literal["legacy", "langgraph"] = "legacy"
    # When true, the SVM classifier runs first and only falls back to the LLM
    # analyzer (and, if still unconfident, human escalation) when its own
    # confidence is below the configured threshold. Requires
    # assets/ML/svm_models.pkl and assets/ML/vectorizer.pkl to both be present.
    # NOTE: defaults to False. The SVM only predicts a message category
    # (otp/reset/locked/vpn/irrelevant); it never extracts the structured
    # operation fields (pdv_code, phone, etc.) that execution needs. As
    # currently wired, a confident SVM call returns an empty operations list,
    # which makes every actionable request escalate to a human instead of
    # auto-executing. Only enable this once FallbackAnalyzer is extended to
    # still run field extraction (LLM or regex-based) even when the SVM is
    # confident about the category.
    use_svm_fallback: bool = False

    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    imap_mailbox: str = "INBOX"
    imap_ssl: bool = True
    imap_poll_seconds: int = 30
    imap_search_criterion: str = "ALL"

    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    smtp_from_address: str = "snoc-agent@example.invalid"
    system_email_address: str = ""
    smtp_ssl: bool = True
    smtp_starttls: bool = False

    authorized_senders: str = ""
    escalation_recipient: str = "human-support@example.invalid"
    sender_imap_mailbox: str = "[Gmail]/All Mail"

    llm_provider: LLMProvider | None = None
    analyzer_provider: LLMProvider | None = None
    verifier_provider: LLMProvider | None = None
    llm_base_url: str = ""
    llm_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    analyzer_model: str = "Qwen2.5-7B-Instruct"
    verifier_model: str = "Qwen3-8B"
    analyzer_temperature: float = 0.0
    verifier_temperature: float = 0.0
    analyzer_min_raw_confidence: float | None = None
    verifier_min_raw_confidence: float | None = None
    qwen3_enable_thinking: bool = False
    qwen3_send_thinking_parameter: bool = True
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_supports_logprobs: bool = False
    llm_json_schema_mode: bool = True
    model_quantization: str = ""

    # Independently hosted OpenAI-compatible vLLM deployments. The role
    # selectors are aliases, while model IDs remain the exact IDs advertised
    # by each deployment's /v1/models response.
    vllm_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    vllm_qwen_base_url: str = "https://qwen.example.com/v1"
    vllm_qwen_model: str = "stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ"
    vllm_gemma_base_url: str = "https://gemma.example.com/v1"
    vllm_gemma_model: str = "google/gemma-4-12B-it"
    vllm_qwen3_30b_base_url: str = "https://qwen3-30b.example.com/v1"
    vllm_qwen3_30b_model: str = "stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ"
    vllm_analyzer_deployment: VLLMDeploymentName = VLLMDeploymentName.QWEN
    vllm_verifier_deployment: VLLMDeploymentName = VLLMDeploymentName.GEMMA
    vllm_request_timeout_seconds: float = 120.0
    vllm_max_retries: int = 2
    vllm_retry_base_seconds: float = 2.0
    vllm_use_json_schema: bool = True
    vllm_allow_json_object_fallback: bool = True
    vllm_allow_prompt_json_fallback: bool = True
    vllm_max_output_tokens_analyzer: int = 4096
    vllm_max_output_tokens_verifier: int = 4096
    vllm_context_window_tokens_analyzer: int = 8192
    vllm_context_window_tokens_verifier: int = 8192
    model_context_safety_margin_tokens: int = 128
    run_vllm_live_tests: bool = False

    evaluation_run_budget_usd: Decimal = Decimal("20")
    evaluation_stop_before_budget_usd: Decimal = Decimal("19")
    evaluation_require_budget_confirmation: bool = False
    evaluation_allow_unknown_cost: bool = True
    evaluation_checkpoint_every: int = 10

    business_api_base_url: str = ""
    business_api_token: SecretStr = Field(default_factory=lambda: SecretStr(""))
    business_api_timeout_seconds: float = 15.0
    business_api_max_retries: int = 2
    business_api_idempotency_guaranteed: bool = False
    business_api_vpn_path: str = "/create-account"
    business_api_otp_path: str = "/update-otp/{pdv_code}/{new_phone}"
    business_api_unblock_path: str = "/unlock-account/{pdv_code}"
    business_api_reset_path: str = "/reset-password/{pdv_code}"
    business_api_vpn_allowed_additional_fields: str = ""
    execution_correction_grace_seconds: int = 30
    allow_subject_body_conflict_auto_execution: bool = False
    allow_forwarded_content_auto_execution: bool = False
    allow_untrusted_workflow_marker_auto_execution: bool = False
    dry_run: bool = True
    dry_run_send_emails: bool = False
    cors_allowed_origins: str = ""
    dashboard_admin_username: str = ""
    dashboard_admin_password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    auth_jwt_secret: SecretStr = Field(default_factory=lambda: SecretStr(""))
    auth_token_ttl_minutes: int = 480

    pdv_pattern: str = r"^\d{8}$"
    phone_pattern: str = r"^\+?\d{9,15}$"
    max_clarification_rounds: int = 1
    enforce_evidence_provenance: bool = True
    store_raw_eml: bool = True
    raw_eml_directory: Path = Path("var/raw_eml")
    log_email_content: bool = False
    max_raw_email_bytes: int = 10 * 1024 * 1024
    max_text_part_bytes: int = 1024 * 1024
    max_html_part_bytes: int = 2 * 1024 * 1024
    max_attachment_count: int = 20
    max_attachment_bytes: int = 5 * 1024 * 1024
    max_model_context_characters: int = 24_000
    max_latest_message_characters: int = 12_000
    max_relevant_thread_characters: int = 4_000

    @field_validator("pdv_pattern", "phone_pattern")
    @classmethod
    def valid_regex(cls, value: str) -> str:
        re.compile(value)
        return value

    @field_validator(
        "imap_poll_seconds",
        "llm_max_retries",
        "vllm_max_retries",
        "business_api_max_retries",
        "execution_correction_grace_seconds",
        "max_clarification_rounds",
    )
    @classmethod
    def non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must be non-negative")
        return value

    @field_validator(
        "vllm_max_output_tokens_analyzer",
        "vllm_max_output_tokens_verifier",
        "vllm_context_window_tokens_analyzer",
        "vllm_context_window_tokens_verifier",
        "model_context_safety_margin_tokens",
        "evaluation_checkpoint_every",
        "max_raw_email_bytes",
        "max_text_part_bytes",
        "max_html_part_bytes",
        "max_attachment_count",
        "max_attachment_bytes",
        "max_model_context_characters",
        "max_latest_message_characters",
        "max_relevant_thread_characters",
        "auth_token_ttl_minutes",
    )
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be at least one")
        return value

    @field_validator("max_model_context_characters")
    @classmethod
    def viable_model_context_limit(cls, value: int) -> int:
        if value < 256:
            raise ValueError("MAX_MODEL_CONTEXT_CHARACTERS must be at least 256")
        return value

    @field_validator(
        "llm_timeout_seconds",
        "vllm_request_timeout_seconds",
        "vllm_retry_base_seconds",
    )
    @classmethod
    def positive_duration(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be greater than zero")
        return value

    @field_validator("evaluation_run_budget_usd", "evaluation_stop_before_budget_usd")
    @classmethod
    def non_negative_money(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("cost limits must be non-negative")
        return value

    @field_validator("analyzer_min_raw_confidence", "verifier_min_raw_confidence")
    @classmethod
    def optional_confidence_threshold(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("confidence threshold must be between 0 and 1")
        return value

    @field_validator(
        "business_api_vpn_path",
        "business_api_otp_path",
        "business_api_unblock_path",
        "business_api_reset_path",
    )
    @classmethod
    def endpoint_path(cls, value: str) -> str:
        if not value.startswith("/") or "://" in value or "\r" in value or "\n" in value:
            raise ValueError("business API endpoint must be a safe absolute-path reference")
        return value

    @field_validator("business_api_vpn_allowed_additional_fields")
    @classmethod
    def safe_vpn_additional_field_names(cls, value: str) -> str:
        reserved = {"pdv_code", "phone", "idempotency_key"}
        names = [name.strip() for name in value.split(",") if name.strip()]
        invalid = [
            name
            for name in names
            if name in reserved or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) is None
        ]
        if invalid:
            raise ValueError(
                "VPN additional field names must be safe, non-reserved identifiers: "
                + ", ".join(sorted(invalid))
            )
        return ",".join(dict.fromkeys(names))

    @model_validator(mode="after")
    def live_execution_has_endpoint(self) -> Settings:
        if not self.dry_run and not self.business_api_base_url:
            raise ValueError("BUSINESS_API_BASE_URL is required when DRY_RUN=false")
        if not self.dry_run and not self.smtp_host:
            raise ValueError("SMTP_HOST is required when DRY_RUN=false")
        if not self.dry_run:
            provider = self.effective_llm_provider
            if provider == LLMProvider.DEMO:
                raise ValueError("a real LLM provider is required when DRY_RUN=false")
            if provider == LLMProvider.OPENAI_COMPATIBLE and not self.llm_base_url:
                raise ValueError("LLM_BASE_URL is required when DRY_RUN=false")
            if provider == LLMProvider.VLLM and not self.effective_vllm_api_key:
                raise ValueError("VLLM_API_KEY is required for vLLM inference")
        if self.smtp_ssl and self.smtp_starttls:
            raise ValueError("SMTP_SSL and SMTP_STARTTLS cannot both be enabled")
        if self.evaluation_stop_before_budget_usd > self.evaluation_run_budget_usd:
            raise ValueError("EVALUATION_STOP_BEFORE_BUDGET_USD cannot exceed the run budget")
        if self.app_environment == "production":
            if self.dry_run:
                raise ValueError("DRY_RUN must be false when APP_ENVIRONMENT=production")
            if self.dry_run_send_emails:
                raise ValueError(
                    "DRY_RUN_SEND_EMAILS is an acceptance-test option and must be false in production"
                )
            if self.effective_llm_provider != LLMProvider.VLLM:
                raise ValueError("LLM_PROVIDER must be vllm in production")
            if self.run_vllm_live_tests:
                raise ValueError("live-test flags must be disabled in production")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("PostgreSQL is required when APP_ENVIRONMENT=production")
            if "x-snoc-test" in self.imap_search_criterion.casefold():
                raise ValueError("test-scoped IMAP search criteria are forbidden in production")
            if self.imap_search_criterion.strip().casefold() == "all":
                raise ValueError("IMAP_SEARCH_CRITERION must be bounded in production")
            required_values = {
                "DATABASE_URL": self.database_url,
                "IMAP_HOST": self.imap_host,
                "IMAP_USERNAME": self.imap_username,
                "IMAP_PASSWORD": self.imap_password.get_secret_value(),
                "SMTP_HOST": self.smtp_host,
                "SMTP_USERNAME": self.smtp_username,
                "SMTP_PASSWORD": self.smtp_password.get_secret_value(),
                "SMTP_FROM_ADDRESS": self.smtp_from_address,
                "AUTHORIZED_SENDERS": self.authorized_senders,
                "ESCALATION_RECIPIENT": self.escalation_recipient,
                "BUSINESS_API_BASE_URL": self.business_api_base_url,
                "BUSINESS_API_TOKEN": self.business_api_token.get_secret_value(),
                "VLLM_API_KEY": self.effective_vllm_api_key,
                "DASHBOARD_ADMIN_USERNAME": self.dashboard_admin_username,
                "DASHBOARD_ADMIN_PASSWORD": self.dashboard_admin_password.get_secret_value(),
                "AUTH_JWT_SECRET": self.auth_jwt_secret.get_secret_value(),
            }
            missing = sorted(name for name, value in required_values.items() if not value.strip())
            if missing:
                raise ValueError(
                    "production configuration is missing required values: " + ", ".join(missing)
                )
            if len(self.auth_jwt_secret.get_secret_value()) < 32:
                raise ValueError("AUTH_JWT_SECRET must contain at least 32 characters")
            placeholder_markers = (
                "replace",
                "placeholder",
                "changeme",
                "example.com",
                "example.invalid",
            )
            placeholders = sorted(
                name
                for name, value in required_values.items()
                if any(marker in value.casefold() for marker in placeholder_markers)
            )
            if placeholders:
                raise ValueError(
                    "production configuration contains placeholder values: "
                    + ", ".join(placeholders)
                )
        return self

    @property
    def effective_llm_provider(self) -> LLMProvider:
        if self.llm_provider is not None:
            return self.llm_provider
        return LLMProvider.OPENAI_COMPATIBLE if self.llm_base_url else LLMProvider.DEMO

    @property
    def effective_analyzer_provider(self) -> LLMProvider:
        if self.analyzer_provider is not None:
            return self.analyzer_provider
        return self.effective_llm_provider

    @property
    def effective_verifier_provider(self) -> LLMProvider:
        if self.verifier_provider is not None:
            return self.verifier_provider
        return self.effective_llm_provider

    @property
    def effective_vllm_api_key(self) -> str:
        return self.vllm_api_key.get_secret_value() or self.llm_api_key.get_secret_value()

    @property
    def vllm_deployments(self) -> tuple[VLLMDeployment, ...]:
        return (
            VLLMDeployment(
                VLLMDeploymentName.QWEN,
                self.vllm_qwen_base_url,
                self.vllm_qwen_model,
            ),
            VLLMDeployment(
                VLLMDeploymentName.GEMMA,
                self.vllm_gemma_base_url,
                self.vllm_gemma_model,
            ),
            VLLMDeployment(
                VLLMDeploymentName.QWEN3_30B,
                self.vllm_qwen3_30b_base_url,
                self.vllm_qwen3_30b_model,
            ),
        )

    @property
    def authorized_sender_set(self) -> set[str]:
        return {
            address.strip().casefold()
            for address in self.authorized_senders.split(",")
            if address.strip()
        }

    @property
    def vpn_allowed_additional_field_set(self) -> frozenset[str]:
        return frozenset(
            name.strip()
            for name in self.business_api_vpn_allowed_additional_fields.split(",")
            if name.strip()
        )

    @property
    def outbound_email_enabled(self) -> bool:
        """Allow real email delivery independently from telecom-operation simulation.

        DRY_RUN always keeps business operations on the mock API. Email delivery remains
        disabled by default in that mode, but production-like mailbox tests may explicitly
        opt in without enabling the real business API.
        """

        return not self.dry_run or self.dry_run_send_emails

    @property
    def effective_system_email_address(self) -> str:
        return self.system_email_address or self.smtp_from_address


def load_settings(env_file: Path | None = None, **overrides: Any) -> Settings:
    """Load settings while allowing tests and CLI callers to inject overrides."""

    if env_file is None:
        return Settings(**overrides)
    return Settings(_env_file=env_file, **overrides)  # type: ignore[call-arg]
