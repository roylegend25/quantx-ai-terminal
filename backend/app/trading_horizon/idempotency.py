"""Fenced, renewable cross-worker execution leases."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis
from sqlalchemy.exc import IntegrityError

from app.db.models import ExecutionFenceCounter, ExecutionIntentAudit, ExecutionIntentLock
from app.db.session import SessionLocal


RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class ExecutionLease:
    scope_key: str
    idempotency_key: str
    owner_token: str
    fencing_token: int
    redis_owned: bool


class DurableIntentCoordinator:
    def __init__(self, ttl_seconds: int = 120, *, session_factory=SessionLocal, redis_client=None) -> None:
        self.ttl_seconds = max(2, ttl_seconds)
        self.session_factory = session_factory
        self._client = redis_client
        self.redis_available: bool | None = None

    def _redis(self):
        if self._client is None:
            self._client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"),
                                                decode_responses=True, socket_timeout=.5)
        return self._client

    @staticmethod
    def keys(authority: dict, requested_key: str | None = None) -> tuple[str, str]:
        scope = ":".join([authority["user_id"], authority["symbol"], authority["engine"]])
        identity = {key: authority[key] for key in (
            "user_id", "symbol", "engine", "profile_decision_id", "execution_timeframe", "direction"
        )}
        canonical = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        idem = hashlib.sha256(f"{canonical}:{requested_key}".encode()).hexdigest() if requested_key else canonical
        return scope, idem

    @staticmethod
    def _redis_key(scope: str) -> str:
        return f"quantx:horizon:intent:{scope}"

    async def acquire(self, authority: dict, requested_key: str | None = None) -> tuple[bool, str | None, ExecutionLease | None]:
        scope, idem = self.keys(authority, requested_key)
        owner_token = uuid.uuid4().hex
        redis_owned = False
        try:
            redis_owned = bool(await self._redis().set(self._redis_key(scope), owner_token, nx=True,
                                                       px=self.ttl_seconds * 1000))
            self.redis_available = True
            if not redis_owned:
                return False, "CONFLICTING_EXECUTION_INTENT", None
        except Exception:
            self.redis_available = False

        now = datetime.now(timezone.utc)
        db = None
        acquired = False
        try:
            db = self.session_factory()
            if db.query(ExecutionIntentAudit).filter_by(idempotency_key=idem).first():
                return False, "DUPLICATE_EXECUTION_INTENT", None
            lock = db.get(ExecutionIntentLock, scope)
            if lock and lock.expires_at.replace(tzinfo=timezone.utc) > now:
                return False, "CONFLICTING_EXECUTION_INTENT", None
            counter = db.get(ExecutionFenceCounter, scope)
            if counter is None:
                counter = ExecutionFenceCounter(scope_key=scope, current_token=1)
                db.add(counter)
                db.flush()
                fence = 1
            else:
                db.query(ExecutionFenceCounter).filter_by(scope_key=scope).update({
                    ExecutionFenceCounter.current_token: ExecutionFenceCounter.current_token + 1
                }, synchronize_session=False)
                db.flush()
                fence = db.get(ExecutionFenceCounter, scope, populate_existing=True).current_token
            expires = now + timedelta(seconds=self.ttl_seconds)
            if lock is None:
                lock = ExecutionIntentLock(scope_key=scope, idempotency_key=idem, owner_token=owner_token,
                    fencing_token=fence, heartbeat_at=now, expires_at=expires)
                db.add(lock)
            else:
                old = db.query(ExecutionIntentAudit).filter_by(idempotency_key=lock.idempotency_key, status="ACTIVE").first()
                if old:
                    old.status = "STALE_FENCED"
                    old.completed_at = now
                updated = db.query(ExecutionIntentLock).filter(
                    ExecutionIntentLock.scope_key == scope,
                    ExecutionIntentLock.expires_at <= now,
                ).update({"idempotency_key": idem, "owner_token": owner_token, "fencing_token": fence,
                          "heartbeat_at": now, "expires_at": expires}, synchronize_session=False)
                if updated != 1:
                    db.rollback()
                    return False, "CONFLICTING_EXECUTION_INTENT", None
            db.add(ExecutionIntentAudit(idempotency_key=idem, scope_key=scope, user_id=authority["user_id"],
                symbol=authority["symbol"], engine=authority["engine"], profile_decision_id=authority["profile_decision_id"],
                execution_timeframe=authority["execution_timeframe"], direction=authority["direction"], status="ACTIVE"))
            db.commit()
            acquired = True
            return True, None, ExecutionLease(scope, idem, owner_token, fence, redis_owned)
        except IntegrityError:
            if db is not None: db.rollback()
            return False, "CONFLICTING_EXECUTION_INTENT", None
        except Exception:
            if db is not None: db.rollback()
            return False, "IDEMPOTENCY_PERSISTENCE_UNAVAILABLE", None
        finally:
            if db is not None:
                db.close()
            if redis_owned and not acquired:
                try:
                    await self._redis().eval(RELEASE_SCRIPT, 1, self._redis_key(scope), owner_token)
                except Exception:
                    pass

    async def renew(self, lease: ExecutionLease) -> bool:
        redis_ok = True
        if lease.redis_owned:
            try:
                redis_ok = bool(await self._redis().eval(RENEW_SCRIPT, 1, self._redis_key(lease.scope_key),
                                                        lease.owner_token, self.ttl_seconds * 1000))
            except Exception:
                self.redis_available = False
                return False
        now = datetime.now(timezone.utc)
        db = self.session_factory()
        try:
            updated = db.query(ExecutionIntentLock).filter_by(scope_key=lease.scope_key,
                owner_token=lease.owner_token, fencing_token=lease.fencing_token).update({
                    "heartbeat_at": now, "expires_at": now + timedelta(seconds=self.ttl_seconds)
                }, synchronize_session=False)
            counter = db.get(ExecutionFenceCounter, lease.scope_key)
            if updated != 1 or counter is None or counter.current_token != lease.fencing_token or not redis_ok:
                db.rollback()
                return False
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
        finally:
            db.close()

    async def owns(self, lease: ExecutionLease) -> bool:
        if lease.redis_owned:
            try:
                if await self._redis().get(self._redis_key(lease.scope_key)) != lease.owner_token:
                    return False
            except Exception:
                return False
        db = self.session_factory()
        try:
            now = datetime.now(timezone.utc)
            lock = db.get(ExecutionIntentLock, lease.scope_key)
            counter = db.get(ExecutionFenceCounter, lease.scope_key)
            return bool(lock and counter and lock.owner_token == lease.owner_token
                and lock.fencing_token == lease.fencing_token == counter.current_token
                and lock.expires_at.replace(tzinfo=timezone.utc) > now)
        except Exception:
            return False
        finally:
            db.close()

    async def heartbeat(self, lease: ExecutionLease, lost: asyncio.Event) -> None:
        try:
            while not lost.is_set():
                await asyncio.sleep(max(.25, self.ttl_seconds / 3))
                if not await self.renew(lease):
                    lost.set()
                    return
        except asyncio.CancelledError:
            return

    async def complete(self, lease: ExecutionLease, result: dict) -> None:
        db = self.session_factory()
        try:
            lock = db.get(ExecutionIntentLock, lease.scope_key)
            audit = db.query(ExecutionIntentAudit).filter_by(idempotency_key=lease.idempotency_key).first()
            if audit:
                audit.status = "TERMINAL"
                audit.result = result
                audit.completed_at = datetime.now(timezone.utc)
            if lock and lock.owner_token == lease.owner_token and lock.fencing_token == lease.fencing_token:
                db.delete(lock)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        if lease.redis_owned:
            try:
                await self._redis().eval(RELEASE_SCRIPT, 1, self._redis_key(lease.scope_key), lease.owner_token)
            except Exception:
                self.redis_available = False


durable_intents = DurableIntentCoordinator()
