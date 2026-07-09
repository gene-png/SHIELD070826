"""Shared helpers for seed loaders.

Loaders are idempotent — they upsert by primary key (or natural key) so
re-running them is safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resolve_packages() -> Path:
    """Locate the ``packages/`` seed-data directory.

    Precedence:
      1. ``SHIELD_SEED_DATA_DIR`` env var (points at the ``packages`` dir), so
         the containers -- where the repo root is not an ancestor of this file
         -- can be told exactly where the mounted data lives.
      2. Repo checkout layout: ``apps/api/scripts/_common.py`` -> ``<repo>/packages``.
      3. Container image layout: ``/app/scripts/_common.py`` -> ``/app/packages``
         (``packages/`` is mounted read-only at ``/app/packages`` for the api
         service; see docker-compose.yml).

    The old code used a hardcoded ``parents[3]`` which raises IndexError inside
    the api container (``/app/scripts/_common.py`` has only three parents).
    """
    override = os.environ.get("SHIELD_SEED_DATA_DIR")
    if override:
        return Path(override)

    here = Path(__file__).resolve()
    parents = here.parents
    candidates: list[Path] = []
    if len(parents) > 3:
        candidates.append(parents[3] / "packages")  # repo checkout
    candidates.append(parents[1] / "packages")  # container: /app/packages
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


PACKAGES = _resolve_packages()
WORKSPACE = PACKAGES.parent


def print_progress(loader: str, message: str) -> None:
    print(f"[seed:{loader}] {message}", flush=True)


def die(loader: str, message: str, *, exit_code: int = 1) -> None:
    print(f"[seed:{loader}] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(exit_code)
