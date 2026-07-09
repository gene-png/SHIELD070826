"""LLM client - the ONLY path that calls an external AI provider.

Master Spec §4.4: provider env-configurable, never hardcoded. §12: every
call MUST pass through the redactor first. AI Prompt §6.13 + §6.14
reinforce both.

Two modes:
  fixture - canned, deterministic responses. Tests + offline dev use this.
  live    - real provider call. Production default for v1 is Anthropic.

The client's `invoke(...)` method:
  1. Redacts the input payload via app.ai.redact.redact_payload.
  2. Writes an `llm_calls` row with status=running BEFORE the provider
     call so a crash mid-call still leaves a record.
  3. Calls the provider (fixture or live).
  4. Updates the llm_calls row with status=completed | failed plus
     token counts + duration + redacted_counts.
  5. Returns the provider response.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from app.ai.redact import RedactionMode, redact_payload
from app.config import Settings, get_settings
from app.logging import correlation_id_var, get_logger
from app.models.llm_call import LLMCall, LLMCallMode, LLMCallStatus

_log = get_logger(__name__)


class LLMConfigurationError(RuntimeError):
    """The LLM is misconfigured in a way that must fail at boot, not on first
    call — e.g. live mode selected but the provider SDK cannot be imported."""


class LLMTimeoutError(RuntimeError):
    """The provider call timed out or the connection failed (FIX E-1b). Routes
    map this to a typed 504; the audit row is still written FAILED and nothing
    is applied."""


def _is_provider_timeout(exc: BaseException) -> bool:
    """True for an Anthropic timeout / connection failure. The SDK is imported
    LAZILY so it never becomes a hard dependency of fixture mode."""
    try:
        import anthropic
    except Exception:  # noqa: BLE001 - SDK absent (fixture mode); can't be a timeout
        return False
    return isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError))


class LLMResponse:
    """Provider response container. Token counts may be None if the provider
    didn't report them (fixture mode supplies them; some providers don't)."""

    __slots__ = ("content", "input_tokens", "output_tokens")

    def __init__(
        self,
        content: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Run the prompt + payload through the provider. Synchronous; the
        caller is on a Celery worker for anything that's not interactive.

        `model` / `max_tokens` are per-call overrides; when None the provider
        uses its configured default."""
        ...


class FixtureProvider:
    """Deterministic canned responses for tests + offline dev.

    A fixture is registered per `purpose`. If the purpose isn't registered,
    `complete()` raises `KeyError` so a test that forgot to register a
    fixture fails loudly rather than silently calling out to the real
    provider.
    """

    name = "fixture"

    def __init__(self, model: str = "fixture-model-1") -> None:
        self.model = model
        self._fixtures: dict[str, Callable[[dict[str, Any]], LLMResponse]] = {}

    def register(self, purpose: str, fn: Callable[[dict[str, Any]], LLMResponse]) -> None:
        self._fixtures[purpose] = fn

    def register_static(self, purpose: str, response: LLMResponse) -> None:
        self.register(purpose, lambda _payload: response)

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # Overrides are irrelevant to canned responses; accepted for protocol
        # parity so a job's model/max_tokens don't change fixture behaviour.
        purpose = payload.get("__purpose__") or "default"
        if purpose not in self._fixtures and "default" not in self._fixtures:
            raise KeyError(
                f"No fixture registered for purpose={purpose!r}. Did you forget "
                "to call FixtureProvider.register()?"
            )
        fn = self._fixtures.get(purpose) or self._fixtures["default"]
        return fn(payload)


class AnthropicProvider:
    """Live Anthropic Claude provider.

    boto3 / anthropic SDKs are heavy and the test runs never hit them, so
    the SDK is imported lazily on first call.
    """

    name = "anthropic"

    def __init__(self, *, model: str, api_key: str) -> None:
        if not api_key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not set. Either set it in .env or switch "
                "SHIELD_LLM_MODE to 'fixture'."
            )
        self.model = model
        self._api_key = api_key
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            # We stream every completion (see complete()), so the timeout is the
            # per-read gap between streamed events, not the whole-response budget
            # — streamed events arrive continuously, so a long generation never
            # trips it. 120s of headroom covers connection setup + first token; a
            # couple of retries recover a transient connection blip.
            self._client = Anthropic(
                api_key=self._api_key,
                max_retries=2,
                timeout=120.0,
            )
        return self._client

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        # Payload is sent as JSON inside the user message. The redactor has
        # already run upstream, so this content is safe to egress.
        import json

        # Per-job overrides fall back to the provider default. 128000 is the
        # configured-default model's max output and gives the full ATT&CK map
        # (~65K tokens even when terse) headroom so it never truncates mid-JSON;
        # a job may pin a smaller cap (e.g. a chunked Haiku job under its 64K).
        effective_model = model or self.model
        effective_max_tokens = max_tokens or 128000

        # STREAM the response. A large job (e.g. the full 600+ technique MITRE
        # ATT&CK map) needs a big max_tokens, and a non-streaming request that
        # size is refused/dropped: the SDK estimates it may exceed the ~10 minute
        # non-streaming ceiling, and long-lived idle sockets get closed by the
        # server ("APIConnectionError: server disconnected"). Streaming keeps the
        # connection alive with continuous events and has no 10-minute cap, so a
        # single large call completes reliably; smaller jobs stop at end_turn
        # long before the cap.
        with client.messages.stream(
            model=effective_model,
            max_tokens=effective_max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": json.dumps(payload)},
                    ],
                }
            ],
        ) as stream:
            msg = stream.get_final_message()
        # `msg.content` is a list of blocks; gather the text blocks.
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        input_tokens = getattr(getattr(msg, "usage", None), "input_tokens", None)
        output_tokens = getattr(getattr(msg, "usage", None), "output_tokens", None)
        return LLMResponse(text, input_tokens, output_tokens)


def _build_provider(settings: Settings) -> LLMProvider:
    if settings.shield_llm_mode == "fixture":
        return FixtureProvider(model=settings.shield_llm_model)
    if settings.shield_llm_provider == "anthropic":
        return AnthropicProvider(
            model=settings.shield_llm_model,
            api_key=settings.anthropic_api_key,
        )
    raise LLMConfigurationError(
        f"LLM provider {settings.shield_llm_provider!r} is not implemented in v1. "
        "Set SHIELD_LLM_PROVIDER=anthropic or SHIELD_LLM_MODE=fixture."
    )


class LLMClient:
    """The blessed surface for AI calls. Routes never construct a provider
    directly; they go through `LLMClient.invoke(...)`."""

    def __init__(self, provider: LLMProvider, settings: Settings | None = None) -> None:
        self.provider = provider
        self._settings = settings or get_settings()

    @property
    def mode(self) -> LLMMode:
        """The active LLM mode, for the run-ai responses to badge simulated
        output (FIX E-5). `fixture` = deterministic canned results; `live` = a
        real provider call."""
        return "fixture" if self._settings.shield_llm_mode == "fixture" else "live"

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        s = settings or get_settings()
        # In live mode the provider SDK is imported lazily on the first call
        # (see AnthropicProvider._ensure_client), so a container missing the SDK
        # would otherwise fail with a generic 500 on the first Run-AI click, not
        # at boot. Verify the import eagerly and fail loudly, naming the package.
        if s.shield_llm_mode == "live" and s.shield_llm_provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError as exc:
                raise LLMConfigurationError(
                    "SHIELD_LLM_MODE=live but the 'anthropic' package cannot be "
                    "imported. Install it (`pip install anthropic`; it is already "
                    "declared in pyproject.toml) or set SHIELD_LLM_MODE=fixture."
                ) from exc
        return cls(_build_provider(s), s)

    def invoke(
        self,
        db: Session,
        *,
        purpose: str,
        prompt: str,
        payload: dict[str, Any],
        requested_by: uuid.UUID,
        service_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        prompt_version: str = "v1",
        redaction_mode: RedactionMode | None = None,
        client_org_name: str | None = None,
        name_hints: tuple[str, ...] = (),
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[LLMResponse, LLMCall]:
        """Redact, write the llm_calls row, call the provider, finalize the row.

        FIX E-2 — the audit trail must survive a request rollback. The llm_calls
        row is written in its OWN short-lived session (an autonomous
        transaction): the RUNNING row is committed BEFORE the provider call and
        the COMPLETED/FAILED update is committed AFTER, both independent of the
        caller's transaction. So when a request errors and its transaction rolls
        back, the FAILED record still stands. The row is returned DETACHED (its
        session is closed); callers read its fields (e.g. `.id`) but must not
        re-add it to their session.

        `db` is used ONLY to resolve the engine bind for that autonomous session
        (`db.get_bind()` does not check out a connection) — this invoke never
        queries or writes through `db`, so the caller can (and the routes do)
        return the pooled connection to the pool across the provider call
        (FIX E-1a).

        `client_id` attributes the spend to a tenant (FIX H-5); when omitted it
        is derived from `service_id`. `model` / `max_tokens` are per-job
        overrides forwarded to the provider; None means the provider default."""
        from app.db.session import open_autonomous_session
        from app.models._common import utcnow as _utcnow
        from app.models.service import Service

        mode = redaction_mode or self._settings.shield_redaction_mode  # type: ignore[assignment]
        cleaned_payload, removed_counts = redact_payload(
            payload,
            mode=mode,
            client_org_name=client_org_name,
            name_hints=name_hints,
        )

        call_mode: LLMCallMode = (
            LLMCallMode.FIXTURE if self._settings.shield_llm_mode == "fixture" else LLMCallMode.LIVE
        )

        # Record the EFFECTIVE model actually used, not the provider default: a
        # per-job override resolves the same way the provider resolves it
        # (`model or default`). The per-tenant cost report reads this row, so it
        # must be truthful about which model billed.
        effective_model = model or self.provider.model

        log_db = open_autonomous_session(db.get_bind())
        try:
            # FIX H-5: derive the tenant from the service when a caller didn't
            # pass it explicitly (e.g. the tech-debt extract path), so every job
            # type still attributes its spend.
            resolved_client_id = client_id
            if resolved_client_id is None and service_id is not None:
                svc = log_db.get(Service, service_id)
                resolved_client_id = svc.client_id if svc is not None else None

            row = LLMCall(
                service_id=service_id,
                client_id=resolved_client_id,
                purpose=purpose,
                prompt_version=prompt_version,
                provider=self.provider.name,
                model=effective_model,
                mode=call_mode,
                status=LLMCallStatus.RUNNING,
                requested_by=requested_by,
                redacted_counts=removed_counts or None,
                correlation_id=correlation_id_var.get(),
            )
            log_db.add(row)
            # Commit the RUNNING row BEFORE the provider call so a crash mid-call
            # still leaves a record; the commit also releases this session's
            # connection, so nothing is held across the provider call.
            log_db.commit()

            # Pass the purpose into the fixture so tests can register per-purpose
            # responses. Real providers ignore it.
            send_payload = {**cleaned_payload, "__purpose__": purpose}

            started = time.monotonic()
            try:
                response = self.provider.complete(
                    prompt, send_payload, model=model, max_tokens=max_tokens
                )
            except Exception as exc:  # noqa: BLE001 - boundary; log + record + re-raise
                row.status = LLMCallStatus.FAILED
                row.error_message = f"{type(exc).__name__}: {exc}"
                row.duration_ms = int((time.monotonic() - started) * 1000)
                log_db.commit()  # FAILED row persists autonomously — survives request rollback
                _log.error(
                    "llm_call_failed",
                    purpose=purpose,
                    provider=self.provider.name,
                    error=row.error_message,
                )
                # FIX E-1b: a provider timeout / connection failure becomes a
                # typed error the routes map to 504; other failures re-raise as-is.
                if _is_provider_timeout(exc):
                    raise LLMTimeoutError("the AI call timed out; nothing was changed") from exc
                raise

            row.status = LLMCallStatus.COMPLETED
            row.input_tokens = response.input_tokens
            row.output_tokens = response.output_tokens
            row.duration_ms = int((time.monotonic() - started) * 1000)
            row.completed_at = _utcnow()
            log_db.commit()
        finally:
            log_db.close()

        _log.info(
            "llm_call_completed",
            purpose=purpose,
            provider=self.provider.name,
            model=effective_model,
            mode=call_mode.value,
            duration_ms=row.duration_ms,
            redacted=removed_counts,
        )
        return response, row


LLMMode = Literal["fixture", "live"]
