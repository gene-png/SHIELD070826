"""H-2: Redis-backed rate limiting on auth + AI endpoints.

These tests build a tiny FastAPI app around RateLimitMiddleware and inject a
fake Redis so no real Redis is required. The limiter is enabled explicitly
here; under the normal suite SHIELD_RATE_LIMIT_ENABLED is unset (False), so
the middleware is inert and cannot trip other tests.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.middleware import ratelimit
from app.middleware.ratelimit import RateLimitMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError


class _FakeRedis:
    """Minimal in-memory stand-in supporting incr/expire."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002 - signature parity
        return True


class _BrokenRedis:
    def incr(self, key: str) -> int:
        raise RedisConnectionError("redis is down")

    def expire(self, key: str, ttl: int) -> bool:
        raise RedisConnectionError("redis is down")


class _RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw: object) -> None:
        self.warnings.append((event, dict(kw)))


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.post("/auth/login")
    def login() -> dict:
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware)
    return app


def _enable(monkeypatch, redis_obj, **overrides) -> None:
    settings = Settings(shield_rate_limit_enabled=True, **overrides)
    monkeypatch.setattr(ratelimit, "get_settings", lambda: settings)
    monkeypatch.setattr(ratelimit, "get_redis_client", lambda url: redis_obj)


@pytest.mark.unit
def test_auth_login_past_threshold_returns_429_with_retry_after(monkeypatch) -> None:
    _enable(monkeypatch, _FakeRedis(), shield_rate_limit_auth_per_min=10)
    client = TestClient(_build_app())

    for _ in range(10):
        assert client.post("/auth/login").status_code == 200

    resp = client.post("/auth/login")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) >= 1
    assert resp.json()["error"]["code"] == 429


@pytest.mark.unit
def test_normal_flow_never_trips(monkeypatch) -> None:
    _enable(monkeypatch, _FakeRedis(), shield_rate_limit_auth_per_min=10)
    client = TestClient(_build_app())

    for _ in range(5):
        assert client.post("/auth/login").status_code == 200


@pytest.mark.unit
def test_redis_unreachable_fails_open_and_logs(monkeypatch) -> None:
    recording = _RecordingLogger()
    monkeypatch.setattr(ratelimit, "logger", recording)
    _enable(monkeypatch, _BrokenRedis(), shield_rate_limit_auth_per_min=1)
    client = TestClient(_build_app())

    # Well past the limit of 1, but Redis is down: every request must succeed.
    for _ in range(5):
        assert client.post("/auth/login").status_code == 200

    events = [e for e, _ in recording.warnings]
    assert "rate_limit_backend_unavailable" in events


@pytest.mark.unit
def test_disabled_by_default_is_inert(monkeypatch) -> None:
    # Enabled flag omitted -> default False. Even with a limit of 1 and a
    # working fake Redis, no request is ever limited.
    settings = Settings(shield_rate_limit_auth_per_min=1)
    monkeypatch.setattr(ratelimit, "get_settings", lambda: settings)
    monkeypatch.setattr(ratelimit, "get_redis_client", lambda url: _FakeRedis())
    client = TestClient(_build_app())

    for _ in range(5):
        assert client.post("/auth/login").status_code == 200
