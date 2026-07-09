"""Load Zero Trust pre-interview questionnaires (CISA / DoD) into `questions`.

Sources (one per framework), transcribed verbatim from the Kentro
SHIELDv2_CISA_ZT_Questionnaire.docx and SHIELDv2_DoD_ZT_Questionnaire.docx:
  packages/zt-data/source/zt_cisa.json
  packages/zt-data/source/zt_dod.json

The "pillar" column carries the ZT pillar (section) name; the
`framework_activities` column carries the ZT capability/activity hints each
prompt informs, so the admin's per-capability scoring grid can light up the
right pillar. (Exact catalog-code mapping is imported with the ZT
cross-references in the Zero Trust service phase.)

Idempotent: upserts on the natural key (framework_key, external_id).

Run it from apps/api:
  python -m scripts.load_zt_questionnaires
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `app` and `scripts` importable when run directly as
# `python scripts/load_zt_questionnaires.py` (the documented container command),
# not only as `python -m scripts.load_zt_questionnaires`. Mirrors seed_demo.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal  # noqa: E402
from app.models.questionnaire import Question  # noqa: E402
from sqlalchemy import select  # noqa: E402

from scripts._common import PACKAGES, print_progress  # noqa: E402

LOADER = "zt_questionnaires"
SOURCES = (
    PACKAGES / "zt-data" / "source" / "zt_cisa.json",
    PACKAGES / "zt-data" / "source" / "zt_dod.json",
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
                    "framework_activities": q.get("framework_activities", []),
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
    print_progress(LOADER, f"upserted {total} questions across CISA / DoD")


if __name__ == "__main__":
    main()
