"""Object storage backends.

Master Spec §11: artifacts live in S3-compatible storage. KMS-encrypted at
rest in production (AWS S3 + KMS or Azure Blob + KMS); MinIO in dev. Tests
use the LocalFilesystemStorage backend so they don't need a network round
trip or a docker stack.

The backend is selected at startup from `Settings.s3_endpoint_url`:
  - empty / "file://..." → LocalFilesystemStorage rooted at that path.
  - "http(s)://..." → S3Storage hitting `Settings.s3_*` credentials.
"""

from app.storage.base import StorageBackend, StorageUnavailable, StoredObject
from app.storage.factory import get_storage

__all__ = ["StorageBackend", "StorageUnavailable", "StoredObject", "get_storage"]
