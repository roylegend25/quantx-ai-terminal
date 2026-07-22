"""Repository-wide guarantee: no production module imports or invokes
Trading Horizon's authority-issuance machinery
(app.trading_horizon.authority, app.trading_horizon.service). The single-
authoritative-decision model (app.decision_engine.execution_gate) is the
only production authority path now.

app.trading_horizon.idempotency (generic execution fencing) and
app.trading_horizon.sizing (generic position-sizing math) are NOT
Horizon-specific and remain legitimately imported by execution_router.py -
this test only forbids the authority-issuance/multi-timeframe-orchestration
modules."""
import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"

# The trading_horizon package itself may reference its own (now historical-
# only) modules internally - that's fine, nothing outside it may.
EXCLUDED_DIRS = {APP_ROOT / "trading_horizon"}

FORBIDDEN_MODULES = {
    "app.trading_horizon.authority",
    "app.trading_horizon.service",
}

FORBIDDEN_CALLS = {
    "evaluate_and_issue_horizon_authority",
    "issue_horizon_authority",
    "validate_horizon_decision",
    "persist_horizon_decision",
    "build_horizon_decision",
}


def _iter_production_files():
    for path in APP_ROOT.rglob("*.py"):
        if any(str(path).startswith(str(excluded)) for excluded in EXCLUDED_DIRS):
            continue
        yield path


def _imported_modules(tree: ast.AST) -> set[str]:
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_no_production_module_imports_horizon_authority_or_service():
    violations = []
    for path in _iter_production_files():
        tree = ast.parse(path.read_text())
        imported = _imported_modules(tree)
        hit = imported & FORBIDDEN_MODULES
        if hit:
            violations.append(f"{path.relative_to(APP_ROOT.parent)}: imports {sorted(hit)}")
    assert not violations, "Production modules still import Trading Horizon authority/service:\n" + "\n".join(violations)


def test_no_production_module_calls_horizon_authority_functions():
    violations = []
    for path in _iter_production_files():
        tree = ast.parse(path.read_text())
        called = _called_names(tree) & FORBIDDEN_CALLS
        if called:
            violations.append(f"{path.relative_to(APP_ROOT.parent)}: calls {sorted(called)}")
    assert not violations, "Production modules still call Trading Horizon authority functions:\n" + "\n".join(violations)


def test_scheduler_and_execution_router_specifically_are_horizon_free():
    """The two most safety-critical production modules, checked explicitly
    (belt and suspenders on top of the repo-wide sweep above)."""
    for relative in ("engine/trading_engine.py", "trading/execution_router.py"):
        path = APP_ROOT / relative
        tree = ast.parse(path.read_text())
        imported = _imported_modules(tree)
        assert not (imported & FORBIDDEN_MODULES), f"{relative} imports {imported & FORBIDDEN_MODULES}"
        called = _called_names(tree)
        assert not (called & FORBIDDEN_CALLS), f"{relative} calls {called & FORBIDDEN_CALLS}"


def test_horizon_preview_route_returns_410():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.api.timeframes as timeframes_module

    client = TestClient(FastAPI())
    client.app.include_router(timeframes_module.router)
    r = client.get("/api/timeframes/BTCUSDT/horizon")
    assert r.status_code == 410
