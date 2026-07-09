"""H-1 guard: the unenforced auth knobs must not be advertised as controls.

`shield_idle_timeout_seconds` / `shield_forced_reauth_seconds` are loaded by
app/config.py but enforced nowhere (see DECISIONS.md D-017). Documentation and
config that present them as active "compensating controls" is worse than absent
for a FedRAMP-target platform, so this test fails if either dead flag name
reappears outside the small allowlist:

  * app/config.py            -- still *defines* the settings (retract, don't rip out)
  * DECISIONS.md             -- the decision log that records the retraction (D-017)
  * FABLE_REMEDIATION_PLAN.md -- the remediation tracker that documents the defect
  * this test file itself
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]

DEAD_FLAGS = (
    "SHIELD_IDLE_TIMEOUT_SECONDS",
    "SHIELD_FORCED_REAUTH_SECONDS",
    "shield_idle_timeout_seconds",
    "shield_forced_reauth_seconds",
)

# Paths (relative to repo root) allowed to mention the dead flags.
ALLOWLIST = {
    Path("apps/api/app/config.py"),
    Path("DECISIONS.md"),
    Path("FABLE_REMEDIATION_PLAN.md"),
    Path("apps/api/tests/unit/test_docs_no_dead_auth_flags.py"),
}

# Directories we never scan.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    "reference-docs",  # binary spec library (docx/xlsx)
}

# Only scan text-ish files.
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".env",
    ".example",
    "",  # e.g. Dockerfile, .env.example handled below
}


def _iter_text_files():
    # os.walk so we can prune SKIP_DIRS in-place (never descend into node_modules,
    # .git, etc.) and never stat their entries -- broken symlinks under
    # node_modules would otherwise raise OSError on Windows.
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
                continue
            yield path


@pytest.mark.unit
def test_dead_auth_flags_absent_outside_allowlist() -> None:
    offenders: list[str] = []
    for path in _iter_text_files():
        rel = path.relative_to(REPO_ROOT)
        if rel in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for flag in DEAD_FLAGS:
            if flag in text:
                offenders.append(f"{rel.as_posix()} contains dead flag '{flag}'")
    message = "Unenforced auth flags reappeared outside the allowlist (H-1 / D-017):\n" + "\n".join(
        offenders
    )
    assert not offenders, message
