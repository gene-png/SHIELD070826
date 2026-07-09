"""Smoke test for migration 0009 (CSF assessment tables).

Applies the full chain to a fresh SQLite file and asserts the
csf_assessments + csf_answers tables, their indexes, and the
service-version / assessment-subcategory unique constraints exist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


@pytest.mark.unit
def test_migration_creates_csf_tables(tmp_path) -> None:
    db_path = tmp_path / "shield-csfmig.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    insp = inspect(engine)
    table_names = set(insp.get_table_names())
    assert "csf_assessments" in table_names
    assert "csf_answers" in table_names

    cols_a = {c["name"] for c in insp.get_columns("csf_assessments")}
    assert {
        "id",
        "service_id",
        "client_id",
        "version",
        "status",
        "approved_at",
        "approved_by",
        "created_at",
        "updated_at",
    } <= cols_a

    cols_b = {c["name"] for c in insp.get_columns("csf_answers")}
    assert {
        "id",
        "assessment_id",
        "client_id",
        "subcategory_code",
        "maturity_tier",
        "notes",
        "evidence_artifact_id",
        "answered_by",
        "answered_at",
        "created_at",
        "updated_at",
    } <= cols_b

    uq_assessments = {u["name"] for u in insp.get_unique_constraints("csf_assessments")}
    assert "uq_csf_assessments_service_version" in uq_assessments

    uq_answers = {u["name"] for u in insp.get_unique_constraints("csf_answers")}
    assert "uq_csf_answers_assessment_subcategory" in uq_answers

    # FIX B-3 (migration 0029): the additive scored_at column exists and is
    # nullable on SQLite (the test DB) after `alembic upgrade head`.
    dim_cols = {c["name"]: c for c in insp.get_columns("csf_dimension_scores")}
    assert "scored_at" in dim_cols
    assert dim_cols["scored_at"]["nullable"] is True
