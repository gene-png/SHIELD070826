"""StorageBackend protocol + small data class."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    """Result of a successful put. Carries enough to populate the Artifact row."""

    key: str
    size_bytes: int
    sha256: str


class StorageUnavailable(RuntimeError):
    """The storage service is temporarily unreachable (FIX C-7).

    Distinct from ``FileNotFoundError``: a missing object is a permanent
    404/410 ("your file is gone"), whereas a credential or connection
    failure means the backend itself is down and the caller should get a
    retryable 503 ("document storage is temporarily unreachable") instead
    of being told the file is lost forever.
    """


def sha256_of(buf: bytes) -> str:
    return hashlib.sha256(buf).hexdigest()


class StorageBackend(Protocol):
    """Minimal surface for v1 artifact storage.

    The protocol stays narrow on purpose: artifacts are written once at
    upload time and read via signed URLs. Mutation isn't a v1 use case.
    """

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        """Write `data` to `key`. Returns size + sha256."""
        ...

    def get(self, key: str) -> bytes:
        """Return the raw bytes at `key`. Raises FileNotFoundError if missing."""
        ...

    def exists(self, key: str) -> bool: ...

    def signed_url(self, key: str, *, ttl_seconds: int = 600) -> str:
        """Return a short-lived read URL for the object at `key`."""
        ...
