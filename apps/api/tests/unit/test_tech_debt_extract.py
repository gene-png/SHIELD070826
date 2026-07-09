"""Unit tests for tech_debt.extract internals (FIX C-4, C-5, C-7).

These exercise the pure helpers directly (numeric coercion, response-shape
validation, storage fetch + error classification) without standing up the
full route stack.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.storage.base import StorageUnavailable
from app.tech_debt.extract import (
    _coerce_item,
    _load_artifact_bytes,
    _parse_number,
    _parse_response,
)

# ---------------------------------------------------------------------------
# C-4: tolerant money/count parsing + confidence clamp + notes fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,200,000", 1_200_000.0),
        ("1,200,000", 1_200_000.0),
        ("1.2M", 1_200_000.0),  # magnitude suffix supported (documented)
        ("500k", 500_000.0),
        ("EUR 1200", 1200.0),
        ("$350000", 350_000.0),
        (350000, 350_000.0),
        ("500 seats", 500.0),
        ("twelve", None),  # no leading number -> falls to notes
        ("", None),
        (None, None),
        (True, None),  # bool is not a real number
    ],
)
def test_parse_number_matrix(raw, expected) -> None:
    assert _parse_number(raw) == expected


@pytest.mark.unit
def test_coerce_item_parses_currency_and_counts() -> None:
    item = _coerce_item(
        {
            "name": "Wiz",
            "annual_cost_usd": "$120,000",
            "license_count": "500 seats",
            "confidence_pct": 92,
        }
    )
    assert item.annual_cost_usd == 120_000.0
    assert item.license_count == 500
    assert item.confidence_pct == 92


@pytest.mark.unit
def test_coerce_item_preserves_unparseable_cost_in_notes() -> None:
    item = _coerce_item({"name": "X", "annual_cost_usd": "twelve", "notes": "seen"})
    assert item.annual_cost_usd is None
    # Raw value preserved for the human reviewer, existing note kept.
    assert "cost: 'twelve'" in item.notes
    assert item.notes.startswith("seen")


@pytest.mark.unit
def test_coerce_item_preserves_unparseable_license_in_notes() -> None:
    item = _coerce_item({"name": "X", "license_count": "site-wide"})
    assert item.license_count is None
    assert "licenses: 'site-wide'" in item.notes


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [(250, 100), (-5, 0), (73, 73), ("101", 100)])
def test_coerce_item_clamps_confidence(raw, expected) -> None:
    assert _coerce_item({"name": "X", "confidence_pct": raw}).confidence_pct == expected


# ---------------------------------------------------------------------------
# C-5: wrong-shape responses must raise, not yield []
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_response_rejects_top_level_list() -> None:
    with pytest.raises(ValueError, match="not the documented shape"):
        _parse_response('[{"name": "Wiz"}]')


@pytest.mark.unit
def test_parse_response_rejects_dict_without_items() -> None:
    with pytest.raises(ValueError, match="not the documented shape"):
        _parse_response('{"capabilities": [{"name": "Wiz"}]}')


@pytest.mark.unit
def test_parse_response_rejects_items_not_a_list() -> None:
    with pytest.raises(ValueError, match="not an array"):
        _parse_response('{"items": {"name": "Wiz"}}')


@pytest.mark.unit
def test_parse_response_accepts_documented_shape() -> None:
    items = _parse_response('{"items": [{"name": "Wiz"}]}')
    assert len(items) == 1
    assert items[0].name == "Wiz"


# ---------------------------------------------------------------------------
# C-7: fetch bytes through the backend; classify storage errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_artifact_bytes_uses_backend_get() -> None:
    calls = {}

    class _Storage:
        def get(self, key: str) -> bytes:
            calls["key"] = key
            return b"payload"

    artifact = SimpleNamespace(file_storage_key="k/1/x.csv")
    assert _load_artifact_bytes(_Storage(), artifact) == b"payload"
    assert calls["key"] == "k/1/x.csv"


def _s3_with_client(client):
    from app.storage.s3 import S3Storage

    s3 = S3Storage.__new__(S3Storage)
    s3._bucket = "bucket"
    s3._kms_key_id = None
    s3._client = client
    return s3


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get_object(self, **_kwargs):
        raise self._exc


@pytest.mark.unit
def test_s3_missing_key_raises_file_not_found() -> None:
    from botocore.exceptions import ClientError

    exc = ClientError({"Error": {"Code": "NoSuchKey", "Message": "no"}}, "GetObject")
    s3 = _s3_with_client(_RaisingClient(exc))
    with pytest.raises(FileNotFoundError):
        s3.get("missing")


@pytest.mark.unit
def test_s3_bad_credentials_raises_storage_unavailable() -> None:
    from botocore.exceptions import ClientError

    exc = ClientError(
        {"Error": {"Code": "InvalidAccessKeyId", "Message": "bad creds"}}, "GetObject"
    )
    s3 = _s3_with_client(_RaisingClient(exc))
    with pytest.raises(StorageUnavailable):
        s3.get("k")


@pytest.mark.unit
def test_s3_no_credentials_raises_storage_unavailable() -> None:
    from botocore.exceptions import NoCredentialsError

    s3 = _s3_with_client(_RaisingClient(NoCredentialsError()))
    with pytest.raises(StorageUnavailable):
        s3.get("k")


@pytest.mark.unit
def test_s3_connection_error_raises_storage_unavailable() -> None:
    from botocore.exceptions import EndpointConnectionError

    exc = EndpointConnectionError(endpoint_url="http://minio:9000")
    s3 = _s3_with_client(_RaisingClient(exc))
    with pytest.raises(StorageUnavailable):
        s3.get("k")


@pytest.mark.unit
def test_s3_get_success_returns_bytes() -> None:
    class _Body:
        def read(self) -> bytes:
            return b"hello"

    class _OkClient:
        def get_object(self, **_kwargs):
            return {"Body": _Body()}

    assert _s3_with_client(_OkClient()).get("k") == b"hello"
