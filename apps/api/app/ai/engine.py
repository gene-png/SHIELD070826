"""AI engine: a small job registry over the single LLM egress path (Work Order C1).

Every AI feature runs through `run_job(job_name, ...)`. A job is just a prompt
template plus a result parser; the engine reuses `LLMClient.invoke` so redaction
and `llm_calls` logging happen once, in one place. Adding a new AI feature is a
new `AIJob` registration — no engine change.

The score/map/synthesize jobs return DRAFT SUGGESTIONS only (scores, statuses,
links, narrative). Deterministic math (totals, tiers, roll-ups) is never done by
the AI — it lives in the per-domain pure functions.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm import LLMClient
from app.models.llm_call import LLMCall


@dataclass(frozen=True)
class AIJob:
    """A registered AI job: a prompt + a parser for the provider's response."""

    name: str
    prompt: str
    parser: Callable[[str], Any]
    prompt_version: str = "v1"
    # The `llm_calls.purpose` + fixture key. Defaults to `name`; tech_debt keeps
    # its historical "extract.capabilities" purpose for fixture compatibility.
    purpose: str | None = None
    # Per-job provider overrides (FIX A-3). When None the provider falls back to
    # its configured default model / max output. Set on high-volume structured
    # jobs to pin a cheaper model with a smaller output cap.
    model: str | None = None
    max_tokens: int | None = None

    @property
    def call_purpose(self) -> str:
        return self.purpose or self.name


@dataclass(frozen=True)
class JobResult:
    data: Any
    llm_call: LLMCall


_REGISTRY: dict[str, AIJob] = {}
_REGISTERED_DEFAULTS = False


def register_job(job: AIJob) -> None:
    _REGISTRY[job.name] = job


def _ensure_defaults() -> None:
    global _REGISTERED_DEFAULTS
    if _REGISTERED_DEFAULTS:
        return
    _REGISTERED_DEFAULTS = True
    # Import for side effect: registers the built-in jobs. Imported lazily to
    # avoid a circular import at module load.
    from app.ai import jobs  # noqa: F401


def get_job(name: str) -> AIJob:
    _ensure_defaults()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"No AI job registered as {name!r}. Registered: {registered_jobs()}"
        ) from exc


def registered_jobs() -> tuple[str, ...]:
    _ensure_defaults()
    return tuple(sorted(_REGISTRY))


def run_job(
    db: Session,
    llm: LLMClient,
    job_name: str,
    *,
    inputs: dict[str, Any],
    requested_by: uuid.UUID,
    service_id: uuid.UUID | None = None,
    client_org_name: str | None = None,
    name_hints: Iterable[str] = (),
) -> JobResult:
    """Run an AI job: redact + log (via LLMClient) + call + parse."""
    job = get_job(job_name)
    response, call_row = llm.invoke(
        db,
        purpose=job.call_purpose,
        prompt=job.prompt,
        payload=inputs,
        requested_by=requested_by,
        service_id=service_id,
        prompt_version=job.prompt_version,
        client_org_name=client_org_name,
        name_hints=tuple(name_hints),
        model=job.model,
        max_tokens=job.max_tokens,
    )
    return JobResult(data=job.parser(response.content), llm_call=call_row)


def parse_json(content: str) -> Any:
    """Best-effort JSON parse of an LLM response, tolerating ```json fences."""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```")
        # ["", "json\n{...}", ""] or ["", "{...}", ""]
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().lower().startswith("json"):
                text = text.lstrip()[4:]
    return json.loads(text.strip())
