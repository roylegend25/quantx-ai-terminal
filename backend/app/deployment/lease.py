from __future__ import annotations

import os
import socket
import uuid

import redis.asyncio as redis

from app.core.config import settings


_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class ExecutionLease:
    """Single renewable execution authority; Redis failure is fail-closed."""

    def __init__(self) -> None:
        self.key = settings.execution_lease_key
        self.ttl = max(10, settings.execution_lease_ttl_seconds)
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        self._client: redis.Redis | None = None
        self.held = False

    def _redis(self) -> redis.Redis:
        if self._client is None:
            url = os.getenv("REDIS_URL", "redis://redis:6379/0")
            self._client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        return self._client

    async def acquire(self) -> bool:
        try:
            self.held = bool(await self._redis().set(self.key, self.owner, nx=True, ex=self.ttl))
        except Exception:
            self.held = False
        return self.held

    async def renew(self) -> bool:
        if not self.held:
            return False
        try:
            self.held = bool(await self._redis().eval(_RENEW, 1, self.key, self.owner, self.ttl))
        except Exception:
            self.held = False
        return self.held

    async def acquire_or_renew(self) -> bool:
        """Keep authority when valid, or reacquire after its TTL elapsed.

        A five-minute scheduler interval is intentionally longer than the
        30-second lease TTL. Redis SET NX still prevents reacquisition when
        another live executor owns the key.
        """
        if self.held and await self.renew():
            return True
        return await self.acquire()

    async def owns(self) -> bool:
        if not self.held:
            return False
        try:
            self.held = await self._redis().get(self.key) == self.owner
        except Exception:
            self.held = False
        return self.held

    async def release(self) -> None:
        try:
            if self.held:
                await self._redis().eval(_RELEASE, 1, self.key, self.owner)
        finally:
            self.held = False

    def public_status(self) -> dict:
        return {"key": self.key, "owner": self.owner if self.held else None,
                "held": self.held, "ttl_seconds": self.ttl}


execution_lease = ExecutionLease()
