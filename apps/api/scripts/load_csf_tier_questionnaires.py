"""Load CSF 2.0 pre-interview questionnaires (HIGH/MOD/LOW) into `questions`.

Sources (one per impact tier), produced by
`apps/api/scripts/extract_csf_questionnaires.py`:
  packages/csf-data/source/csf_tier_high.json
  packages/csf-data/source/csf_tier_moderate.json
  packages/csf-data/source/csf_tier_low.json

The "pillar" column carries the questionnaire section name; the
`framework_activities` column carries the CSF 2.0 subcategory ids so the gap
engine can map answers back to NIST controls.

Idempotent: upserts on the natural key (framework_key, external_id) via a
dialect-agnostic get-or-update so it runs on both the SQLite dev DB and
Postgres.

Run it from apps/api:
  python -m scripts.load_csf_tier_questionnaires
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `app` and `scripts` importable when run directly as
# `python scripts/load_csf_tier_questionnaires.py` (the documented container
# command), not only as `python -m scripts.load_csf_tier_questionnaires`.
# Mirrors seed_demo.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.questionnaire import Question  # noqa: E402
from sqlalchemy import select  # noqa: E402

from scripts._common import PACKAGES, print_progress  # noqa: E402

LOADER = "csf_tier_questionnaires"
SOURCES = (
    PACKAGES / "csf-data" / "source" / "csf_tier_high.json",
    PACKAGES / "csf-data" / "source" / "csf_tier_moderate.json",
    PACKAGES / "csf-data" / "source" / "csf_tier_low.json",
)


def main() -> None:
    total = 0
    with SessionLocal() as db:
        for source in SOURCES:
            data = json.loads(source.read_text(encoding="utf-8"))
            framework_key = data["framework_key"]
            print_progress(
                LOADER,
                f"loading {len(data['questions'])} questions for {framework_key}",
            )
            for q in data["questions"]:
                values = {
                    "framework_key": framework_key,
                    "external_id": q["external_id"],
                    "pillar": q["section_name"],
                    "order_index": int(q.get("order_index", 0)),
                    "stem": q["stem"],
                    "cues": q.get("cues", []),
                    "phase": None,
                    "framework_activities": q.get("csf_subcategories", []),
                }
                existing = db.scalar(
                    select(Question).where(
                        Question.framework_key == framework_key,
                        Question.external_id == q["external_id"],
                    )
                )
                if existing is None:
                    db.add(Question(**values))
                else:
                    # phase is intentionally left untouched on update.
                    for col in (
                        "pillar",
                        "order_index",
                        "stem",
                        "cues",
                        "framework_activities",
                    ):
                        setattr(existing, col, values[col])
                total += 1
        db.commit()
    print_progress(LOADER, f"upserted {total} questions across HIGH / MOD / LOW")


if __name__ == "__main__":
    main()
