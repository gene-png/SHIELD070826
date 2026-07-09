"""Zero Trust scoring + gap engine.

Pure functions. Given a `capability_code -> maturity_stage` map for a
chosen framework, produces:
  - Overall maturity stage (band-cutoff label)
  - Per-pillar stage rollup with coverage + weakest codes
  - Top-N prioritized gaps against a target stage

Framework awareness: the labels (Traditional/Initial/... for CISA,
Baseline/Target/... for DoD) are picked from the maturity module.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.zt.catalog import (
    Capability,
    capabilities,
    pillars,
)
from app.zt.maturity import (
    MaturityStage,
    ZtFrameworkCode,
    level_count,
    stage_label,
)

WEAKEST_PER_PILLAR = 5
DEFAULT_TARGET_STAGE = int(MaturityStage.STAGE_3)
DEFAULT_TOP_N = 20

# Pillar weights for gap prioritization. Identity + Data score highest
# in the typical FedRAMP / DoD risk picture because they sit closest to
# the protected resources; supporting pillars carry a 1.0 baseline.
_PILLAR_WEIGHTS: dict[str, float] = {
    # CISA codes
    "ID": 1.20,  # Identity
    "DT": 1.15,  # Data
    "DV": 1.10,  # Devices
    "NW": 1.05,  # Networks
    "AW": 1.10,  # Applications & Workloads
    "VA": 1.00,  # Visibility & Analytics (cross-cutting)
    "AO": 1.00,  # Automation & Orchestration (cross-cutting)
    "GV": 1.00,  # Governance (cross-cutting)
    # DoD codes
    "USR": 1.20,
    "DAT": 1.15,
    "DEV": 1.10,
    "NET": 1.05,
    "APP": 1.10,
    "VIS": 1.00,
    "AUT": 1.00,
}


@dataclass(frozen=True)
class PillarScoreResult:
    pillar_code: str
    pillar_name: str
    capability_count: int
    answered_count: int
    average_stage: float | None
    # average_stage normalized to a percentage of the framework's max level
    # (so a DoD pillar at "3" and a CISA pillar at "4" both read 100%).
    maturity_pct: float | None
    coverage_pct: float
    weakest_capability_codes: tuple[str, ...]


@dataclass(frozen=True)
class ScoreResult:
    framework: ZtFrameworkCode
    total_capabilities: int
    answered_capabilities: int
    coverage_pct: float
    average_stage: float | None
    maturity_pct: float | None
    overall_stage_label: str
    by_pillar: tuple[PillarScoreResult, ...]


@dataclass(frozen=True)
class Gap:
    code: str
    pillar_code: str
    pillar_name: str
    name: str
    outcome: str
    current_stage: int
    target_stage: int
    gap_size: int
    priority_score: float
    notes: str | None


@dataclass(frozen=True)
class GapAnalysis:
    framework: ZtFrameworkCode
    target_stage: int
    target_label: str
    gaps: tuple[Gap, ...]
    unscored_codes: tuple[str, ...]
    total_gap_count: int
    gap_count_by_pillar: dict[str, int]


def _coverage_pct(answered: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round(answered / total * 100, 1)


def _round_average(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _validated(stage: int | None, framework: ZtFrameworkCode) -> int | None:
    """Coerce a stored stage to a valid level for the framework, else None.

    Valid stages are 1..level_count(framework) (CISA 1-4, DoD 1-3). Anything
    out of range (including the retired DoD stage 0) is treated as unscored.
    """
    if stage is None:
        return None
    value = int(stage)
    if 1 <= value <= level_count(framework):
        return value
    return None


def _maturity_pct(avg: float | None, framework: ZtFrameworkCode) -> float | None:
    """Average stage as a percentage of the framework's top level."""
    if avg is None:
        return None
    return round(avg / level_count(framework) * 100, 1)


def _label_from_average(avg: float | None, framework: ZtFrameworkCode) -> str:
    if avg is None:
        return "Unscored"
    # Round to the nearest stage, clamped into the framework's range.
    stage = max(1, min(level_count(framework), round(avg)))
    return stage_label(stage, framework)


def _pillar_name_lookup(framework: ZtFrameworkCode) -> dict[str, str]:
    return {p.code: p.name for p in pillars(framework)}


def compute(framework: ZtFrameworkCode, answers: Mapping[str, int | None]) -> ScoreResult:
    names = _pillar_name_lookup(framework)
    pillar_results: list[PillarScoreResult] = []
    overall_values: list[int] = []
    total = 0
    answered = 0

    for p in pillars(framework):
        codes = [c.code for c in capabilities(framework) if c.pillar_code == p.code]
        pillar_total = len(codes)
        total += pillar_total

        scored_pairs: list[tuple[str, int]] = []
        for code in codes:
            s = _validated(answers.get(code), framework)
            if s is not None:
                scored_pairs.append((code, s))

        answered += len(scored_pairs)
        overall_values.extend(s for _, s in scored_pairs)

        scored_pairs.sort(key=lambda p: (p[1], p[0]))
        weakest = tuple(code for code, _ in scored_pairs[:WEAKEST_PER_PILLAR])

        pillar_avg = _round_average([s for _, s in scored_pairs])
        pillar_results.append(
            PillarScoreResult(
                pillar_code=p.code,
                pillar_name=names.get(p.code, p.code),
                capability_count=pillar_total,
                answered_count=len(scored_pairs),
                average_stage=pillar_avg,
                maturity_pct=_maturity_pct(pillar_avg, framework),
                coverage_pct=_coverage_pct(len(scored_pairs), pillar_total),
                weakest_capability_codes=weakest,
            )
        )

    avg_overall = _round_average(overall_values)
    return ScoreResult(
        framework=framework,
        total_capabilities=total,
        answered_capabilities=answered,
        coverage_pct=_coverage_pct(answered, total),
        average_stage=avg_overall,
        maturity_pct=_maturity_pct(avg_overall, framework),
        overall_stage_label=_label_from_average(avg_overall, framework),
        by_pillar=tuple(pillar_results),
    )


def _row_for(
    cap: Capability,
    current: int,
    target: int,
    notes: str | None,
    pillar_name: str,
) -> Gap:
    gap_size = max(0, target - current)
    weight = _PILLAR_WEIGHTS.get(cap.pillar_code, 1.0)
    priority = round(gap_size * weight, 2)
    return Gap(
        code=cap.code,
        pillar_code=cap.pillar_code,
        pillar_name=pillar_name,
        name=cap.name,
        outcome=cap.outcome,
        current_stage=current,
        target_stage=target,
        gap_size=gap_size,
        priority_score=priority,
        notes=notes,
    )


def analyze_gaps(
    framework: ZtFrameworkCode,
    answers: Mapping[str, int | None],
    *,
    notes: Mapping[str, str | None] | None = None,
    target_stage: int = DEFAULT_TARGET_STAGE,
    targets: Mapping[str, int | None] | None = None,
    top_n: int | None = DEFAULT_TOP_N,
) -> GapAnalysis:
    """Gaps where current < target. `targets` supplies per-capability targets
    (Work Order D3); a capability with no per-capability target falls back to
    the engagement-level `target_stage`."""
    max_stage = level_count(framework)
    if not (1 <= target_stage <= max_stage):
        target_stage = min(DEFAULT_TARGET_STAGE, max_stage)
    notes = notes or {}
    targets = targets or {}
    names = _pillar_name_lookup(framework)

    def _target_for(code: str) -> int:
        t = targets.get(code)
        if isinstance(t, int) and 1 <= t <= max_stage:
            return t
        return target_stage

    rows: list[Gap] = []
    unscored: list[str] = []
    for cap in capabilities(framework):
        s = _validated(answers.get(cap.code), framework)
        if s is None:
            unscored.append(cap.code)
            continue
        cap_target = _target_for(cap.code)
        if s >= cap_target:
            continue
        pillar_name = names.get(cap.pillar_code, cap.pillar_code)
        rows.append(_row_for(cap, s, cap_target, notes.get(cap.code), pillar_name))

    rows.sort(key=lambda g: (-g.priority_score, g.code))

    by_pillar: dict[str, int] = {}
    for g in rows:
        by_pillar[g.pillar_code] = by_pillar.get(g.pillar_code, 0) + 1

    return GapAnalysis(
        framework=framework,
        target_stage=target_stage,
        target_label=stage_label(target_stage, framework),
        gaps=tuple(rows[:top_n]),
        unscored_codes=tuple(unscored),
        total_gap_count=len(rows),
        gap_count_by_pillar=by_pillar,
    )


@dataclass(frozen=True)
class RoadmapItem:
    month: int  # 1..horizon_months
    code: str
    pillar_code: str
    pillar_name: str
    name: str
    current_stage: int
    target_stage: int
    priority_score: float


def build_roadmap(gaps: Sequence[Gap], *, horizon_months: int = 12) -> tuple[RoadmapItem, ...]:
    """Sequence prioritized gaps across a fixed horizon (Work Order D3).

    `gaps` are assumed already ordered by descending priority (as analyze_gaps
    returns them). Identity/User and Data pillars already carry higher weight in
    the priority score, so they naturally land in earlier months. The list is
    spread evenly so each month gets roughly the same number of items.
    """
    n = len(gaps)
    if n == 0 or horizon_months <= 0:
        return ()
    per_month = max(1, math.ceil(n / horizon_months))
    return tuple(
        RoadmapItem(
            month=min(horizon_months, i // per_month + 1),
            code=g.code,
            pillar_code=g.pillar_code,
            pillar_name=g.pillar_name,
            name=g.name,
            current_stage=g.current_stage,
            target_stage=g.target_stage,
            priority_score=g.priority_score,
        )
        for i, g in enumerate(gaps)
    )


__all__ = [
    "DEFAULT_TARGET_STAGE",
    "DEFAULT_TOP_N",
    "Gap",
    "GapAnalysis",
    "PillarScoreResult",
    "RoadmapItem",
    "ScoreResult",
    "WEAKEST_PER_PILLAR",
    "analyze_gaps",
    "build_roadmap",
    "compute",
]
