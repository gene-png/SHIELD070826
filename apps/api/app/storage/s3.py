"""S3 / S3-compatible storage backend (MinIO in dev; AWS S3 + KMS in prod).

boto3 is a heavy import; resolve it lazily so test runs that never touch
S3 don't pay the import cost.
"""

from __future__ import annotations

from app.config import Settings
from app.storage.base import StorageBackend, StorageUnavailable, StoredObject, sha256_of

# S3 error codes that mean the object (or bucket) genuinely isn't there. These
# map to FileNotFoundError -> 404/410. Everything else boto raises (bad
# credentials, access denied, endpoint down, timeout) is a service problem and
# maps to StorageUnavailable -> 503 (FIX C-7).
_MISSING_KEY_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "NotFound", "404"})


class S3Storage(StorageBackend):
    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.client import Config

        self._bucket = settings.s3_bucket
        self._kms_key_id = settings.s3_kms_key_id
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            # FIX C-7: bound every call so a stalled MinIO/S3 can't hang an API
            # worker indefinitely. connect/read timeouts + a small retry budget
            # fail fast into a typed 503 rather than blocking forever.
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        kwargs: dict = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        # KMS encryption is mandatory in production. The dev-stub key id
        # short-circuits the SSE arg so MinIO doesn't reject the request.
        if self._kms_key_id and self._kms_key_id != "dev-stub-key":
            kwargs["ServerSideEncryption"] = "aws:kms"
            kwargs["SSEKMSKeyId"] = self._kms_key_id
        self._client.put_object(**kwargs)
        return StoredObject(key=key, size_bytes=len(data), sha256=sha256_of(data))

    def get(self, key: str) -> bytes:
        # FIX C-7: classify the failure instead of collapsing EVERY boto error
        # into FileNotFoundError. Only a genuine missing key/bucket is a 410;
        # credential and connection failures are a 503 (StorageUnavailable).
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _MISSING_KEY_CODES:
                raise FileNotFoundError(key) from exc
            raise StorageUnavailable(f"S3 get_object failed ({code or 'unknown error'}).") from exc
        except BotoCoreError as exc:
            # NoCredentialsError, EndpointConnectionError, Connect/ReadTimeout,
            # etc. - the backend is unreachable/misconfigured, not the object.
            raise StorageUnavailable(f"S3 storage is unreachable: {exc}") from exc
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 - boto raises a family of errors here
            return False

    def signed_url(self, key: str, *, ttl_seconds: int = 600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )
