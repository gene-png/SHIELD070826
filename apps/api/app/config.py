"""Settings loaded from environment. Master Spec §4.4-§4.5; AI Prompt §6.14.

No setting may be hardcoded. Every external service and security knob is here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
RedactionMode = Literal["strict", "standard", "off"]
LLMProvider = Literal["anthropic", "openai", "azure_openai", "bedrock", "gemini", "local"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Runtime
    environment: Environment = "development"
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://shield:shield@db:5432/shield"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Object storage
    s3_endpoint_url: str = "http://minio:9000"
    s3_bucket: str = "shield-artifacts"
    s3_access_key: str = "shield-minio"
    s3_secret_key: str = (
        "shield-minio-secret"  # noqa: S105 - dev placeholder, refused in prod via assert_safe_for_runtime
    )
    s3_kms_key_id: str = "dev-stub-key"

    # OIDC (Keycloak)
    keycloak_issuer: str = "http://keycloak:8080/realms/shield"
    keycloak_audience: str = "shield-api"
    keycloak_client_id: str = "shield-web"

    # LLM (Master Spec §4.4 - never hardcoded)
    shield_llm_provider: LLMProvider = "anthropic"
    shield_llm_model: str = "claude-sonnet-5"
    shield_llm_mode: Literal["fixture", "live"] = "fixture"
    anthropic_api_key: str = ""

    # Bootstrap admin service account. When email+password are set, the app
    # provisions exactly one admin with this email at startup (idempotent);
    # self-registration never creates admins. Leave empty to skip seeding.
    shield_bootstrap_admin_email: str = ""
    shield_bootstrap_admin_password: str = ""
    shield_bootstrap_admin_name: str = "SHIELD Admin"

    # Retention: deactivated accounts with no login for this many days are
    # permanently purged by the maintenance job (app.maintenance.retention).
    shield_user_purge_idle_days: int = Field(default=365, ge=1)

    # Run bootstrap-admin seeding + retention purge at API startup. Disabled in
    # the test suite (which overrides the DB session per-test).
    shield_run_startup_maintenance: bool = True

    # Feature flags (Master Spec §2 - deferred for v1)
    shield_auth_require_mfa: bool = False
    shield_auth_require_email_verify: bool = False
    shield_email_delivery_enabled: bool = False

    # Redaction (Master Spec §12)
    shield_redaction_mode: RedactionMode = "strict"

    # Session security (Master Spec §4.5)
    jwt_access_ttl_seconds: int = Field(default=900, ge=60)
    jwt_refresh_ttl_seconds: int = Field(default=1800, ge=300)
    shield_account_lockout_max_attempts: int = Field(default=10, ge=1)
    shield_account_lockout_window_seconds: int = Field(default=900, ge=60)
    shield_idle_timeout_seconds: int = Field(default=1800, ge=60)
    shield_forced_reauth_seconds: int = Field(default=86400, ge=300)

    # JWT signing
    jwt_signing_secret: str = (
        "dev-only-replace-via-secrets-manager"  # noqa: S105 - dev placeholder, refused in prod via assert_safe_for_runtime
    )

    # Mail (MailHog in dev)
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_from: str = "no-reply@shield.local"

    def is_production(self) -> bool:
        return self.environment == "production"

    def assert_safe_for_runtime(self) -> None:
        """Reject obviously unsafe configurations at startup."""
        if self.is_production() and self.shield_redaction_mode == "off":
            raise RuntimeError(
                "SHIELD_REDACTION_MODE=off is forbidden when ENVIRONMENT=production "
                "(Master Spec §12)."
            )
        if self.is_production() and self.jwt_signing_secret.startswith("dev-only"):
            raise RuntimeError("JWT_SIGNING_SECRET is still the default placeholder in production.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
