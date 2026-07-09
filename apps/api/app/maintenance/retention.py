"""User-account retention purge (crypto-shred / anonymize).

Admins deactivate accounts rather than hard-deleting them (see
`app.routes.admin`). This job permanently erases the PII of accounts that have
been deactivated and idle (no login) past the retention window.

Why anonymize instead of row-delete: `audit_entries` is append-only (DB trigger
+ ORM guard) and references `users.id` via `actor_user_id ON DELETE SET NULL`.
A row-delete would fire that SET NULL against the immutable audit table and be
rejected, so deleting a user who has any audit history is impossible. Instead we
crypto-shred: scrub every PII field and keep the (now anonymous) row, which both
satisfies "right to erasure" and preserves the integrity of the audit trail.

Safety rules:
  - Only `is_active == False` accounts are eligible. Active accounts are never
    auto-purged, no matter how long since their last login.
  - "Idle" is measured from the most recent of (last_login_at, deactivated_at,
    created_at), so a recently deactivated account is not purged early.
  - `purged_at` makes the job idempotent: already-shredded rows are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.logging import get_logger
from app.models._common import utcnow
from app.models.user import User

_log = get_logger(__name__)

DEFAULT_MAX_IDLE_DAYS = 365

# Argon2 never produces this, so verify_password can never match it -> the
# shredded account can never authenticate.
_UNUSABLE_PASSWORD_HASH = "!purged"  # noqa: S105 - sentinel, not a real secret  # nosec B105


@dataclass
class PurgeSummary:
    purged: int = 0


def purge_stale_users(db: Session, *, max_idle_days: int = DEFAULT_MAX_IDLE_DAYS) -> PurgeSummary:
    """Crypto-shred deactivated accounts idle past `max_idle_days`.

    Commits its own work. Returns a summary of how many were purged.
    """
    summary = PurgeSummary()
    cutoff = utcnow() - timedelta(days=max_idle_days)

    candidates = (
        db.execute(
            select(User).where(
                User.is_active.is_(False),
                User.purged_at.is_(None),
                or_(User.last_login_at.is_(None), User.last_login_at < cutoff),
                or_(User.deactivated_at.is_(None), User.deactivated_at < cutoff),
                User.created_at < cutoff,
            )
        )
        .scalars()
        .all()
    )

    now = utcnow()
    for user in candidates:
        # Audit references actor_user_id, but we keep the (anonymized) row, so
        # the reference stays valid. Record the purge with no PII in details.
        audit(
            db,
            action="user.purged",
            target_type="user",
            target_id=user.id,
            actor_user_id=None,
            details={"max_idle_days": max_idle_days},
        )
        user.email = f"purged-{user.id}@deleted.example"
        user.display_name = None
        user.title = None
        user.phone = None
        user.password_hash = _UNUSABLE_PASSWORD_HASH
        user.is_active = False
        user.purged_at = now
        summary.purged += 1

    db.commit()
    if summary.purged:
        _log.info("user_retention_purge_complete", purged=summary.purged)
    return summary
