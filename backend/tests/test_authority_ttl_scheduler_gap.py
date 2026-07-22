"""Stage 1 performance audit primary root cause: authority expires_at was
a flat 120s regardless of execution timeframe, while the scheduler only
reissues authority every scheduler_interval_seconds (300s) - a 180s gap
every cycle with no valid persisted authority, during which fast dashboard
polling fell back to a ~60-85s, 6-timeframe evaluation cascade. This
proves every profile's authority window now safely exceeds the scheduler
cadence, closing that gap."""
from datetime import datetime, timezone

from app.core.config import settings
from app.trading_horizon.service import PROFILES, build_horizon_decision

from tests.test_horizon_authority import frames


def test_every_profile_authority_window_exceeds_scheduler_cadence():
    for profile_key, profile in PROFILES.items():
        values = frames()
        now = datetime.now(timezone.utc)
        decision = build_horizon_decision("BTCUSDT", values, profile_key, user_id="ttl-gap-test",
                                          engine_version="2.2.0", now=now)
        generated_at = datetime.fromisoformat(decision["generated_at"])
        expires_at = datetime.fromisoformat(decision["expires_at"])
        window_seconds = (expires_at - generated_at).total_seconds()
        assert window_seconds >= settings.scheduler_interval_seconds, (
            f"{profile_key} ({profile.execution_timeframe}) authority window is {window_seconds}s, "
            f"shorter than the {settings.scheduler_interval_seconds}s scheduler cadence - the cascade gap is back"
        )


def test_short_term_profile_window_matches_its_own_candle_duration_floor():
    """5m execution timeframe: the window must be at least one 5m candle,
    not an arbitrary shorter constant."""
    values = frames()
    now = datetime.now(timezone.utc)
    decision = build_horizon_decision("BTCUSDT", values, "short_term", user_id="ttl-gap-test-2",
                                      engine_version="2.2.0", now=now)
    generated_at = datetime.fromisoformat(decision["generated_at"])
    expires_at = datetime.fromisoformat(decision["expires_at"])
    assert (expires_at - generated_at).total_seconds() >= 300
