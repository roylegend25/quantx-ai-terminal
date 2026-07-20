import asyncio

from app.db.models import ExecutionFenceCounter, ExecutionIntentAudit, ExecutionIntentLock
from app.db.session import SessionLocal
from app.trading_horizon.idempotency import DurableIntentCoordinator, RELEASE_SCRIPT, RENEW_SCRIPT


class FakeRedis:
    def __init__(self):
        self.now_ms=0; self.values={}; self.available=True
    def advance(self,ms): self.now_ms+=ms
    def _get(self,key):
        value=self.values.get(key)
        if value and value[1]<=self.now_ms: self.values.pop(key,None); return None
        return value
    async def set(self,key,value,nx=False,px=None,ex=None):
        if not self.available: raise ConnectionError
        if nx and self._get(key): return False
        ttl=px if px is not None else ex*1000
        self.values[key]=(value,self.now_ms+ttl); return True
    async def get(self,key):
        if not self.available: raise ConnectionError
        value=self._get(key); return value[0] if value else None
    async def eval(self,script,numkeys,key,*args):
        if not self.available: raise ConnectionError
        value=self._get(key)
        if not value or value[0]!=args[0]: return 0
        if script==RENEW_SCRIPT:
            self.values[key]=(value[0],self.now_ms+int(args[1])); return 1
        if script==RELEASE_SCRIPT:
            self.values.pop(key,None); return 1
        raise AssertionError("unexpected script")


def reset(user):
    db=SessionLocal(); db.query(ExecutionIntentLock).filter(ExecutionIntentLock.scope_key.like(f"{user}:%")).delete(synchronize_session=False)
    db.query(ExecutionIntentAudit).filter_by(user_id=user).delete(); db.query(ExecutionFenceCounter).filter(ExecutionFenceCounter.scope_key.like(f"{user}:%")).delete(synchronize_session=False); db.commit(); db.close()


def auth(user="fence-user",decision="d1",direction="LONG"):
    return {"user_id":user,"symbol":"BTCUSDT","engine":"active_drive_v2","profile_decision_id":decision,
            "execution_timeframe":"15m","direction":direction}


def test_fake_redis_nx_owner_renew_release_and_ttl():
    reset("fence-user"); fake=FakeRedis(); one=DurableIntentCoordinator(2,redis_client=fake); two=DurableIntentCoordinator(2,redis_client=fake)
    ok,_,lease=asyncio.run(one.acquire(auth())); assert ok and lease and lease.fencing_token==1
    assert asyncio.run(two.acquire(auth(decision="d2")))[1]=="CONFLICTING_EXECUTION_INTENT"
    key=one._redis_key(lease.scope_key); fake.advance(1500); assert asyncio.run(one.renew(lease))
    fake.advance(1000); assert asyncio.run(one.owns(lease))
    wrong=type(lease)(lease.scope_key,lease.idempotency_key,"wrong",lease.fencing_token,True)
    assert not asyncio.run(one.renew(wrong)); asyncio.run(one.complete(wrong,{})); assert asyncio.run(fake.get(key))==lease.owner_token
    asyncio.run(one.complete(lease,{"ok":True})); assert asyncio.run(fake.get(key)) is None


def test_fencing_progression_stale_owner_and_redis_disagreement():
    reset("fence-stale"); fake=FakeRedis(); coordinator=DurableIntentCoordinator(2,redis_client=fake)
    ok,_,old=asyncio.run(coordinator.acquire(auth("fence-stale"))); assert ok
    fake.advance(2500)
    db=SessionLocal(); lock=db.get(ExecutionIntentLock,old.scope_key); lock.expires_at=lock.created_at; db.commit(); db.close()
    ok,_,new=asyncio.run(coordinator.acquire(auth("fence-stale","d2"))); assert ok and new.fencing_token==2
    assert not asyncio.run(coordinator.owns(old)); assert asyncio.run(coordinator.owns(new))
    fake.values[coordinator._redis_key(new.scope_key)]=(old.owner_token,fake.now_ms+2000)
    assert not asyncio.run(coordinator.owns(new))


def test_redis_outage_uses_database_and_database_failure_is_closed():
    reset("fence-outage"); fake=FakeRedis(); fake.available=False
    coordinator=DurableIntentCoordinator(2,redis_client=fake)
    assert asyncio.run(coordinator.acquire(auth("fence-outage")))[0]
    assert asyncio.run(coordinator.acquire(auth("fence-outage","d2")))[1]=="CONFLICTING_EXECUTION_INTENT"
    class BrokenSession:
        def __call__(self): raise RuntimeError("db unavailable")
    broken=DurableIntentCoordinator(2,session_factory=BrokenSession(),redis_client=fake)
    # Construction/acquisition failure must never produce a lease.
    try: result=asyncio.run(broken.acquire(auth("broken")))
    except RuntimeError: result=(False,"IDEMPOTENCY_PERSISTENCE_UNAVAILABLE",None)
    assert result[0] is False


def test_heartbeat_renews_slow_route_beyond_initial_ttl():
    reset("fence-slow"); fake=FakeRedis(); coordinator=DurableIntentCoordinator(2,redis_client=fake)
    ok,_,lease=asyncio.run(coordinator.acquire(auth("fence-slow"))); assert ok
    async def slow():
        lost=asyncio.Event(); task=asyncio.create_task(coordinator.heartbeat(lease,lost))
        await asyncio.sleep(2.4)
        owned=await coordinator.owns(lease)
        lost.set(); task.cancel(); await asyncio.gather(task,return_exceptions=True)
        return owned
    assert asyncio.run(slow()) is True


def test_redis_reconnect_never_overrides_database_conflict():
    reset("fence-reconnect"); fake=FakeRedis(); fake.available=False
    coordinator=DurableIntentCoordinator(2,redis_client=fake)
    assert asyncio.run(coordinator.acquire(auth("fence-reconnect")))[0]
    fake.available=True
    result=asyncio.run(coordinator.acquire(auth("fence-reconnect","second")))
    assert result[0] is False and result[1]=="CONFLICTING_EXECUTION_INTENT"
