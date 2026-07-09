#!/usr/bin/env bash
# Seed a running compose stack: demo tenant + both questionnaire loaders.
#
# Usage (from the repo root, with the stack already up):
#     bash scripts/seed.sh
#
# Prereqs: `docker compose up -d db redis minio createbuckets api` first.
# The api container has packages/ mounted read-only (E-6) and resolves the
# seed data via SHIELD_SEED_DATA_DIR (defaults to /app/packages).
#
# All three steps are idempotent (upsert by natural key), so re-running is safe.
set -euo pipefail

cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }

echo "[seed] 1/3 demo tenant (users, services, a released deliverable)..."
compose exec -T api python scripts/seed_demo.py

echo "[seed] 2/3 Zero Trust questionnaires (CISA / DoD)..."
compose exec -T api python scripts/load_zt_questionnaires.py

echo "[seed] 3/3 CSF 2.0 tier questionnaires (HIGH / MOD / LOW)..."
compose exec -T api python scripts/load_csf_tier_questionnaires.py

echo "[seed] done."
