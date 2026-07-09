"""Redis-backed rate limiting (H-2).

The API had no rate limiting: ``/auth/*`` was exposed to credential stuffing /
bulk registration at line speed, and the synchronous AI endpoints to
concurrency that burns real API spend and DB connections. This middleware adds
a small fixed-window limiter on top of the Redis that docker-compose already
runs (and which had no consumer before this).

Design notes:
  - Algorithm: fixed window per (scope, identifier, window). A single Redis
    ``INCR`` (plus ``EXPIRE`` on first hit) per request - cheap and atomic
    enough for abuse protection. We deliberately avoid pulling in slowapi:
    the whole feature is ~40 lines and Redis is already a dependency.
  - Buckets:
      * auth endpoints  -> per client IP     (limit: auth_per_min)
      * AI  endpoints   -> per authenticated user id (limit: ai_per_min)
    AI limits compose with a later per-assessment lock (E-3), owned elsewhere.
  - FAILS OPEN. If Redis is unreachable we log a structured warning and let the
    request through. A limiter outage must never take the API down.
  - Inert unless SHIELD_RATE_LIMIT_ENABLED=true. The test suite and local dev
    never set that env var, so the limiter is a no-op there and cannot cause
    flaky failures. A per-bucket limit <= 0 also disables that bucket.
  - Emits a structured ``rate_limit_exceeded`` log line on every 429 and a
    ``rate_limit_backend_unavailable`` warning on fail-open, via structlog.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import Settings, get_settings
from app.logging import get_logger
from app.security.jwt import TokenError, verify_token

logger = get_logger(__name__)

# Endpoints limited per client IP (unauthenticated attack surface).
_AUTH_PATHS = frozenset({"/auth/login", "/auth/register", "/auth/refresh"})

# Endpoints limited per authenticated user. Matched by path suffix so we don't
# have to import (or edit) the concurrently-owned AI route modules; every
# synchronous run-ai / generate / extract endpoint ends with one of these.
_AI_SUFFIXES = ("/run-ai", "/generate", "/extract")

# Cached Redis client. Wrapped in a helper so tests can monkeypatch it.
_redis_client: Redis | None = None


def get_redis_client(url: str) -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(url, decode_responses=True)
    return _redis_client


@dataclass(frozen=True)
class _Rule:
    scope: str
    limit: int
    identifier: str | None


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _user_id_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = verify_token(token, expected_type="access")
    except TokenError:
        return None
    return str(payload.sub)


def _match_rule(request: Request, settings: Settings) -> _Rule | None:
    """Return the rate-limit rule for this request, or None to skip."""
    if request.method != "POST":
        return None

    path = request.url.path
    if path in _AUTH_PATHS:
        limit = settings.shield_rate_limit_auth_per_min
        if limit <= 0:
            return None
        return _Rule(scope="auth", limit=limit, identifier=_client_ip(request))

    if any(path.endswith(suffix) for suffix in _AI_SUFFIXES):
        limit = settings.shield_rate_limit_ai_per_min
        if limit <= 0:
            return None
        # No valid access token -> let the route's own auth return 401. We
        # only meter authenticated callers here.
        user_id = _user_id_from_request(request)
        if user_id is None:
            return None
        return _Rule(scope="ai", limit=limit, identifier=user_id)

    return None


def _check_limit(
    client: Redis, scope: str, identifier: str, limit: int, window_seconds: int
) -> tuple[bool, int]:
    """Fixed-window check. Returns (allowed, retry_after_seconds).

    Fails OPEN (returns allowed=True) if Redis is unreachable.
    """
    now = int(time.time())
    window = now // window_seconds
    key = f"ratelimit:{scope}:{identifier}:{window}"
    try:
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)
        if count > limit:
            reset_at = (window + 1) * window_seconds
            return False, max(1, reset_at - now)
        return True, 0
    except (RedisError, OSError) as exc:
        # Fail open: a limiter outage must not take the API down.
        logger.warning("rate_limit_backend_unavailable", scope=scope, error=str(exc))
        return True, 0


def _rate_limited_response(request: Request, rule: _Rule, retry_after: int) -> JSONResponse:
    cid = getattr(request.state, "correlation_id", "unknown")
    logger.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        method=request.method,
        scope=rule.scope,
        limit=rule.limit,
        retry_after=retry_after,
        correlation_id=cid,
    )
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": 429,
                "message": "Rate limit exceeded. Please retry later.",
                "correlation_id": cid,
            }
        },
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        if not settings.shield_rate_limit_enabled:
            return await call_next(request)

        rule = _match_rule(request, settings)
        if rule is None or rule.identifier is None:
            return await call_next(request)

        client = get_redis_client(settings.redis_url)
        # Redis client is synchronous; offload so the event loop isn't blocked.
        allowed, retry_after = await run_in_threadpool(
            _check_limit,
            client,
            rule.scope,
            rule.identifier,
            rule.limit,
            settings.shield_rate_limit_window_seconds,
        )
        if not allowed:
            return _rate_limited_response(request, rule, retry_after)
        return await call_next(request)
