"""Paper vs Binance Real settings scope separation, audit trail, and safe
copy workflow (Bot Settings Part 1/2/3)."""
from app.db.models import RiskSettingsAudit
from app.db.session import SessionLocal
from app.risk import settings_repository


def teardown_function(_fn):
    db = SessionLocal()
    try:
        db.query(RiskSettingsAudit).delete()
        db.commit()
    finally:
        db.close()


def test_paper_and_binance_real_are_separate_rows():
    db = SessionLocal()
    try:
        settings_repository.update_settings({"min_confidence_to_trade": 0.42}, scope="paper", changed_by="tester", db=db)
        settings_repository.update_settings({"min_confidence_to_trade": 0.91}, scope="binance_real", changed_by="tester", db=db)
        paper = settings_repository.get_settings(scope="paper", db=db)
        real = settings_repository.get_settings(scope="binance_real", db=db)
        assert paper["min_confidence_to_trade"] == 0.42
        assert real["min_confidence_to_trade"] == 0.91
    finally:
        db.close()


def test_saving_paper_never_affects_binance_real():
    db = SessionLocal()
    try:
        before = settings_repository.get_settings(scope="binance_real", db=db)
        settings_repository.update_settings({"min_point_margin": 12.0}, scope="paper", changed_by="tester", db=db)
        after = settings_repository.get_settings(scope="binance_real", db=db)
        assert after["min_point_margin"] == before["min_point_margin"]
    finally:
        db.close()


def test_saving_point_margin_and_total_evidence_persists_per_scope():
    db = SessionLocal()
    try:
        result = settings_repository.update_settings(
            {"min_point_margin": 8.5, "min_total_evidence": 15.0}, scope="binance_real", changed_by="tester", db=db)
        assert result["min_point_margin"] == 8.5
        assert result["min_total_evidence"] == 15.0
        reloaded = settings_repository.get_settings(scope="binance_real", db=db)
        assert reloaded["min_point_margin"] == 8.5
        assert reloaded["min_total_evidence"] == 15.0
    finally:
        db.close()


def test_unknown_scope_rejected():
    db = SessionLocal()
    try:
        try:
            settings_repository.get_settings(scope="nonsense", db=db)
            assert False, "expected InvalidScope"
        except settings_repository.InvalidScope:
            pass
    finally:
        db.close()


def test_audit_trail_records_previous_new_scope_version_reason():
    db = SessionLocal()
    try:
        before = settings_repository.get_settings(scope="paper", db=db)
        settings_repository.update_settings(
            {"min_confidence_to_trade": 0.77}, scope="paper", changed_by="alice", reason="loosen for backtest", db=db)
        history = settings_repository.get_audit_history(scope="paper", db=db)
        row = next(h for h in history if h["field"] == "min_confidence_to_trade")
        assert row["previous_value"] == before["min_confidence_to_trade"]
        assert row["new_value"] == 0.77
        assert row["scope"] == "paper"
        assert row["changed_by"] == "alice"
        assert row["reason"] == "loosen for backtest"
        assert row["configuration_version"] > (before["version"] or 1) - 1
    finally:
        db.close()


def test_reset_to_defaults_per_scope():
    db = SessionLocal()
    try:
        settings_repository.update_settings({"min_confidence_to_trade": 0.05}, scope="paper", changed_by="tester", db=db)
        settings_repository.reset_settings(scope="paper", changed_by="tester", db=db)
        after = settings_repository.get_settings(scope="paper", db=db)
        assert after["min_confidence_to_trade"] == settings_repository.DEFAULTS["min_confidence_to_trade"]
    finally:
        db.close()


def test_copy_paper_to_binance_real_copies_all_fields_but_never_touches_live_state():
    db = SessionLocal()
    try:
        settings_repository.update_settings(
            {"min_confidence_to_trade": 0.55, "min_point_margin": 6.0, "max_open_positions": 3},
            scope="paper", changed_by="tester", db=db)
        result = settings_repository.copy_settings("paper", "binance_real", changed_by="tester", reason="align", db=db)
        assert result["min_confidence_to_trade"] == 0.55
        assert result["min_point_margin"] == 6.0
        assert result["max_open_positions"] == 3

        history = settings_repository.get_audit_history(scope="binance_real", db=db)
        assert any(h["change_kind"] == "copy_from_paper" for h in history)

        # Structural guarantee: settings_repository never imports app.trading.modes.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(settings_repository))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
        assert "app.trading.modes" not in imported_modules
    finally:
        db.close()


def test_copy_from_scope_equal_to_scope_rejected():
    db = SessionLocal()
    try:
        try:
            settings_repository.copy_settings("paper", "paper", changed_by="tester", db=db)
            assert False, "expected InvalidScope"
        except settings_repository.InvalidScope:
            pass
    finally:
        db.close()


def test_concurrent_style_updates_each_bump_version_and_audit():
    """Two sequential updates to the same scope must each produce their own
    audit rows and a monotonically increasing version - a stand-in for
    concurrent-write safety since sqlite in these tests is single-writer."""
    db = SessionLocal()
    try:
        r1 = settings_repository.update_settings({"cooldown_minutes": 5}, scope="paper", changed_by="a", db=db)
        r2 = settings_repository.update_settings({"cooldown_minutes": 10}, scope="paper", changed_by="b", db=db)
        assert r2["version"] > r1["version"]
        history = settings_repository.get_audit_history(scope="paper", db=db)
        cooldown_changes = [h for h in history if h["field"] == "cooldown_minutes"]
        assert len(cooldown_changes) >= 2
    finally:
        db.close()
