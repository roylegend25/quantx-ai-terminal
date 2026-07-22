"""In-app notification centre for indicator/settings events (Bot Settings
Part 8): structural dedup, unread count, mark-read."""
from app.db.session import SessionLocal
from app.decision_engine import indicator_notifications


def _cleanup(db, source_name):
    db.query(indicator_notifications.IndicatorNotification).filter_by(source_name=source_name).delete()
    db.commit()


def test_dedup_by_event_source_symbol_timeframe_version():
    db = SessionLocal()
    try:
        first = indicator_notifications.notify(
            indicator_notifications.EVENT_MOVED_TO_SHADOW_ONLY, title="t", source_name="dedup-test",
            symbol="BTCUSDT", timeframe="5m", evaluation_version=1, db=db)
        second = indicator_notifications.notify(
            indicator_notifications.EVENT_MOVED_TO_SHADOW_ONLY, title="t", source_name="dedup-test",
            symbol="BTCUSDT", timeframe="5m", evaluation_version=1, db=db)
        third_new_version = indicator_notifications.notify(
            indicator_notifications.EVENT_MOVED_TO_SHADOW_ONLY, title="t", source_name="dedup-test",
            symbol="BTCUSDT", timeframe="5m", evaluation_version=2, db=db)
        assert first is not None
        assert second is None  # exact repeat, deduped
        assert third_new_version is not None  # genuinely new version, not deduped

        rows = [n for n in indicator_notifications.list_notifications(db=db)["notifications"] if n["source_name"] == "dedup-test"]
        assert len(rows) == 2
    finally:
        _cleanup(db, "dedup-test")
        db.close()


def test_unread_count_and_mark_read():
    db = SessionLocal()
    try:
        row = indicator_notifications.notify(
            indicator_notifications.EVENT_RECOMMENDED_FOR_REACTIVATION, title="star", source_name="unread-test",
            symbol="ETHUSDT", timeframe="15m", evaluation_version=1, db=db)
        listing = indicator_notifications.list_notifications(db=db)
        assert listing["unread"] >= 1
        marked = indicator_notifications.mark_read(row["id"], db=db)
        assert marked["read"] is True
        listing_only_unread = indicator_notifications.list_notifications(unread_only=True, db=db)
        assert row["id"] not in [n["id"] for n in listing_only_unread["notifications"]]
    finally:
        _cleanup(db, "unread-test")
        db.close()


def test_mark_all_read():
    db = SessionLocal()
    try:
        indicator_notifications.notify(indicator_notifications.EVENT_CONFIG_THRESHOLD_CHANGED, title="a",
                                        source_name="mark-all-test-a", evaluation_version=1, db=db)
        indicator_notifications.notify(indicator_notifications.EVENT_CONFIG_THRESHOLD_CHANGED, title="b",
                                        source_name="mark-all-test-b", evaluation_version=1, db=db)
        updated = indicator_notifications.mark_all_read(db=db)
        assert updated >= 2
        assert indicator_notifications.list_notifications(unread_only=True, db=db)["unread"] == 0
    finally:
        _cleanup(db, "mark-all-test-a")
        _cleanup(db, "mark-all-test-b")
        db.close()


def test_unknown_event_rejected():
    db = SessionLocal()
    try:
        try:
            indicator_notifications.notify("not_a_real_event", title="x", db=db)
            assert False, "expected ValueError"
        except ValueError:
            pass
    finally:
        db.close()
