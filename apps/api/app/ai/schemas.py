"""Shared JSON response shapes for the AI jobs — one source of truth (FIX A-2).

Why this module exists: the prompt text and the route's parser had no shared
source of truth, so they drifted. A compliant live response matched zero rows
and the platform silently did nothing (e.g. the CSF prompt demanded
`{"subcategories": [{"code": ...}]}` while the route read `data["scores"]` keyed
by `tier|subcategory_code`). Every prompt now interpolates its shape constant
from here, and the routes/tests validate against the same constant. Never
restate a shape inline in a prompt again — change it here and both sides move
together.

One module-level constant per AI job. Each is a string suitable for direct
interpolation into a prompt body.
"""

from __future__ import annotations

# tech_debt_extract — capability extraction from a raw tool inventory.
# (The tech_debt prompt lives in app.tech_debt.extract; this constant is the
# shared shape the routes/tests validate against.)
TECH_DEBT_EXTRACT_SHAPE = """{"items": [{"name": "<short name>",
"vendor": "<vendor or null>",
"category": "<category like CNAPP, EDR, SIEM, IAM, GRC, or null>",
"function": "<one-line function the capability serves, or null>",
"annual_cost_usd": <number or null>, "license_count": <integer or null>,
"notes": "<short note, or null>", "confidence_pct": <integer 0-100>,
"source_row_index": <integer index into rows[]>}]}"""

# csf_score — five dimension scores per (tier, subcategory_code). The route keys
# rows by f"{tier}|{subcategory_code}", so BOTH fields are mandatory.
CSF_SCORE_SHAPE = """{"scores": [{"tier": "high|moderate|low",
"subcategory_code": "GV.OC-01", "governance": 0-2, "policy": 0-2,
"implementation": 0-2, "monitoring": 0-2, "improvement": 0-2,
"what_we_found": "..."}], "executive_summary": "..."}"""

# zt_score — current/target maturity suggestions per capability.
ZT_SCORE_SHAPE = """{"capabilities": [{"code": "...", "current": int,
"target": int}], "pillar_narratives": {"<pillar_code>": "..."},
"executive_summary": "...", "roadmap_summary": "..."}"""

# mitre_map — ATT&CK coverage status per technique. Compact by design: "gap" and
# "not_applicable" entries carry only technique_code + status.
MITRE_MAP_SHAPE = """{"techniques": [
{"technique_code": "T1003", "status": "covered", "detection_tools": [...],
"prevention_tools": [...], "response_tools": [...], "rationale": "..."},
{"technique_code": "T1005", "status": "gap"}
], "executive_summary": "...", "top_blind_spots": [...]}"""

# risk_synthesize — candidate Risk Register entries. likelihood, impact,
# recommended_action, and axis are CLOSED sets whose tokens must match the
# lowercase snake_case StrEnum values in app.risk.engine exactly — code derives
# the tier from likelihood + impact, so an off-vocabulary token is dropped.
RISK_SYNTHESIZE_SHAPE = """{"entries": [{"title": "...", "description": "...",
"axis": "detection|prevention|response", "linked_techniques": [...],
"linked_controls": [...], "likelihood": "very_low|low|medium|high|very_high",
"impact": "negligible|minor|moderate|major|catastrophic",
"compensating_controls": "...", "residual_risk": "...",
"recommended_action": "remediate|mitigate|accept|transfer|avoid",
"rationale": "...", "source": "coverage_finding|questionnaire_response",
"source_id": "..."}]}"""
