"""Default fixture-mode responses for the five AI jobs (FIX X-8).

``SHIELD_LLM_MODE=fixture`` is the ``docker compose up`` default, so an operator
running the platform offline (no API key) must still get working Run-AI buttons
and a Tech-Debt extract. Before this module, ``_build_provider`` returned a bare
``FixtureProvider`` whose registry was EMPTY, so every purpose raised
``KeyError`` and every AI route 500'd.

These builders fix that WITHOUT fabricating security analysis — the platform's
whole reason to exist. Two hard rules govern every builder here:

* Every ENTITY in a response (subcategory/technique/capability code, tool name,
  vendor, source id, title) is echoed from the incoming payload. Nothing names a
  tool, technique, control, or vendor that was not handed to it.
* Every NON-entity value (scores, statuses, likelihood/impact tokens, narratives)
  is a fixed, deterministic, obviously-simulated constant — never random, never
  convincing. The "Simulated" badge (FIX E-5) is earned by transparency.

Each builder is a pure ``payload dict -> LLMResponse(json_string)`` function with
no randomness and no timestamps, so the same payload always yields byte-identical
JSON. ``register_default_fixtures`` wires all five onto a provider; it is called
from ``_build_provider`` (NOT from ``FixtureProvider.__init__`` — a freshly
constructed provider must still raise on an unregistered purpose).
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.llm import FixtureProvider, LLMResponse
from app.logging import get_logger

_log = get_logger(__name__)

# One visible marker so a human is never fooled: these strings are meant to look
# synthetic on sight, in every narrative field they land in.
_SIM = "[SIMULATED — fixture mode]"
_SIM_NARRATIVE = f"{_SIM} Placeholder finding generated offline; not a real assessment."
_SIM_SUMMARY = f"{_SIM} Placeholder executive summary generated offline without an AI provider."
_SIM_ROADMAP = f"{_SIM} Placeholder remediation roadmap generated offline."
_SIM_RISK_DESC = f"{_SIM} Placeholder risk narrative grounded only on the supplied finding."
_SIM_RISK_CONTROLS = f"{_SIM} Compensating controls not evaluated offline."
_SIM_RISK_RESIDUAL = f"{_SIM} Residual risk not evaluated offline."
_SIM_RISK_RATIONALE = f"{_SIM} Deterministic placeholder; likelihood/impact are fixed constants."
_SIM_EXTRACT_NOTE = (
    f"{_SIM} Fields derived mechanically from this row; confidence is a placeholder."
)


def _build_csf_score(payload: dict[str, Any]) -> LLMResponse:
    """One score entry per payload item, echoing that item's own tier + code.

    Dimension values are a fixed valid integer (1); the tier and subcategory_code
    come straight from the chunk the model was handed, so the route's
    ``tier|subcategory_code`` keying matches every seeded row.
    """
    from app.routes.csf import _DIM_FIELDS

    chunk_tier = payload.get("tier")
    scores: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        code = item.get("subcategory_code")
        if not code:
            continue
        entry: dict[str, Any] = {
            "tier": item.get("tier", chunk_tier),
            "subcategory_code": code,
        }
        for dim in _DIM_FIELDS:
            entry[dim] = 1
        entry["what_we_found"] = _SIM_NARRATIVE
        scores.append(entry)
    return LLMResponse(json.dumps({"scores": scores, "executive_summary": _SIM_SUMMARY}))


def _build_zt_score(payload: dict[str, Any]) -> LLMResponse:
    """One entry per capability code in the payload; current/target = 1/2.

    1 and 2 are valid on both the CISA (1-4) and DoD (1-3) scales, so the route's
    range clamp keeps them. pillar_narratives is left empty rather than inventing
    per-pillar prose.
    """
    caps: list[dict[str, Any]] = []
    for code in payload.get("capabilities") or []:
        if isinstance(code, str) and code:
            caps.append({"code": code, "current": 1, "target": 2})
    return LLMResponse(
        json.dumps(
            {
                "capabilities": caps,
                "pillar_narratives": {},
                "executive_summary": _SIM_SUMMARY,
                "roadmap_summary": _SIM_ROADMAP,
            }
        )
    )


def _build_mitre_map(payload: dict[str, Any]) -> LLMResponse:
    """One entry per technique code, every one marked ``gap``.

    An all-gap simulated map is honest: it claims no coverage it cannot ground.
    Marking a technique "covered" would require asserting a tool defends it,
    which offline mode has no basis for — so we never do. Compact form: gap
    entries carry only technique_code + status, per the shape.
    """
    techniques: list[dict[str, Any]] = []
    for code in payload.get("technique_codes") or []:
        if isinstance(code, str) and code:
            techniques.append({"technique_code": code, "status": "gap"})
    return LLMResponse(
        json.dumps(
            {
                "techniques": techniques,
                "executive_summary": _SIM_SUMMARY,
                "top_blind_spots": [],
            }
        )
    )


def _build_risk_synthesize(payload: dict[str, Any]) -> LLMResponse:
    """One entry per finding, grounded entirely on that finding.

    title/source/source_id/description trace to the finding; linked_techniques is
    the finding's own technique (a coverage finding's ``source_id`` IS an ATT&CK
    code) filtered to the payload's ``valid_techniques`` — never an invented one;
    a questionnaire finding links its control code instead. likelihood, impact,
    axis and recommended_action are fixed valid enum tokens.
    """
    from app.risk.engine import Impact, Likelihood, RecommendedAction, RiskAxis

    valid_techniques = {t for t in (payload.get("valid_techniques") or []) if isinstance(t, str)}
    valid_controls = {c for c in (payload.get("valid_controls") or []) if isinstance(c, str)}

    entries: list[dict[str, Any]] = []
    for finding in payload.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        source = finding.get("source")
        source_id = finding.get("source_id")
        label = finding.get("label") or f"{source} {source_id}"

        linked_techniques: list[str] = []
        linked_controls: list[str] = []
        if source == "coverage_finding" and isinstance(source_id, str) and source_id:
            # A coverage finding's source_id is itself the ATT&CK technique code.
            if source_id in valid_techniques:
                linked_techniques = [source_id]
        elif isinstance(source_id, str) and source_id and source_id in valid_controls:
            linked_controls = [source_id]

        entries.append(
            {
                "title": f"{_SIM} {label}",
                "description": _SIM_RISK_DESC,
                "axis": RiskAxis.DETECTION.value,
                "linked_techniques": linked_techniques,
                "linked_controls": linked_controls,
                "likelihood": Likelihood.MEDIUM.value,
                "impact": Impact.MODERATE.value,
                "compensating_controls": _SIM_RISK_CONTROLS,
                "residual_risk": _SIM_RISK_RESIDUAL,
                "recommended_action": RecommendedAction.MITIGATE.value,
                "rationale": _SIM_RISK_RATIONALE,
                "source": source,
                "source_id": source_id,
            }
        )
    return LLMResponse(json.dumps({"entries": entries}))


def _first_nonempty(row: dict[str, Any]) -> str | None:
    for value in row.values():
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _cell_by_header(row: dict[str, Any], keywords: tuple[str, ...]) -> str | None:
    for header, value in row.items():
        h = str(header).lower()
        if any(k in h for k in keywords) and value is not None and str(value).strip():
            return str(value).strip()
    return None


def _build_tech_debt_extract(payload: dict[str, Any]) -> LLMResponse:
    """One item per NON-BLANK row, every field derived from THAT row's cells.

    ``source_row_index`` is the real index into ``rows[]``; ``name``/``vendor``/
    ``annual_cost_usd``/``license_count`` are read from the row's own cells (by
    header keyword, falling back to the first non-empty cell for the name). No
    tool, vendor, or cost is ever invented; blank rows are skipped.
    """
    from app.tech_debt.extract import _parse_number

    rows = payload.get("rows") or []
    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = _cell_by_header(
            row,
            (
                "name",
                "tool",
                "product",
                "capability",
                "service",
                "software",
                "platform",
                "application",
            ),
        ) or _first_nonempty(row)
        if not name:
            continue  # blank/empty row — skip, mirroring the real extractor

        vendor = _cell_by_header(row, ("vendor", "publisher", "manufacturer", "maker"))
        raw_cost = _cell_by_header(row, ("cost", "price", "spend", "annual", "amount", "usd"))
        cost = _parse_number(raw_cost) if raw_cost is not None else None
        raw_lic = _cell_by_header(
            row, ("license", "seat", "qty", "quantity", "count", "users", "user")
        )
        lic_num = _parse_number(raw_lic) if raw_lic is not None else None

        items.append(
            {
                "name": name,
                "vendor": vendor,
                "category": None,
                "function": None,
                "annual_cost_usd": cost,
                "license_count": int(lic_num) if lic_num is not None else None,
                "notes": _SIM_EXTRACT_NOTE,
                "confidence_pct": 50,
                "source_row_index": idx,
            }
        )
    return LLMResponse(json.dumps({"items": items}))


def register_default_fixtures(provider: FixtureProvider) -> None:
    """Register a grounded builder for every AI purpose on ``provider``.

    Called from ``_build_provider`` in fixture mode. Purpose keys match
    ``AIJob.call_purpose`` (tech-debt keeps its historical
    ``extract.capabilities`` purpose).
    """
    provider.register("csf_score", _build_csf_score)
    provider.register("zt_score", _build_zt_score)
    provider.register("mitre_map", _build_mitre_map)
    provider.register("risk_synthesize", _build_risk_synthesize)
    provider.register("extract.capabilities", _build_tech_debt_extract)
    _log.info("fixture_defaults_registered", purposes=5)
