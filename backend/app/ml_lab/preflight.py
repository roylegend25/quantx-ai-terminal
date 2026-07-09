"""Pre-training health gate.

Runs before any scheduled or train-now batch: verifies there is enough
historical data, that feature generation works end-to-end, that the
validated data store's most recent quality reports clear the configured
score/gap thresholds, and that the host itself is healthy enough (RAM,
disk, database) to fit models without destabilizing the live backend.

Every check reports what it measured - a skipped training run can always
say exactly why. Thresholds live in the dashboard-editable lab settings
(app/ml_lab/settings_repo.py, section "training_gate").
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import DataQualityReport
from app.db.session import SessionLocal

# Quality reports older than this describe data the next training run will
# not use anyway; they inform but do not gate.
QUALITY_REPORT_MAX_AGE = timedelta(hours=48)

MODELS_DISK_PATH = Path("/app/data")


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _dataset_checks(gate: dict, params: dict | None) -> list[dict]:
    from app.ml_lab import market_dataset

    spec = {
        "symbols": (params or {}).get("symbols") or market_dataset.DEFAULT_SPEC["symbols"],
        "timeframe": (params or {}).get("timeframe") or market_dataset.DEFAULT_SPEC["timeframe"],
        "bars": (params or {}).get("bars") or market_dataset.DEFAULT_SPEC["bars"],
        "horizon": (params or {}).get("horizon") or market_dataset.DEFAULT_SPEC["horizon"],
    }
    info = market_dataset.dataset_info(spec)
    if not info.get("available"):
        return [
            _check("historical_data", False, info.get("reason") or "Dataset unavailable"),
            _check("feature_generation", False, "Not evaluated - dataset could not be built"),
        ]

    min_rows = int(gate.get("min_dataset_rows", 500))
    rows = info.get("rows", 0)
    checks = [
        _check(
            "historical_data",
            rows >= min_rows,
            f"{rows} labeled bars available for {','.join(info.get('symbols', []))} (need >= {min_rows})",
        )
    ]

    features = info.get("features", 0)
    checks.append(
        _check(
            "feature_generation",
            features > 0,
            f"{features} features computed by the live indicator pipeline",
        )
    )

    # a label balance pinned to one class means the forward-return labeling
    # degenerated (flat/broken series) - training would learn nothing real
    balance = info.get("label_balance")
    if balance is not None:
        checks.append(
            _check(
                "label_balance",
                0.05 <= balance <= 0.95,
                f"{balance:.1%} of labels are 'up' (need 5%-95%)",
            )
        )
    return checks


def _quality_checks(gate: dict, params: dict | None, db: Session) -> list[dict]:
    from app.ml_lab import market_dataset

    symbols = [s.upper() for s in ((params or {}).get("symbols") or market_dataset.DEFAULT_SPEC["symbols"])]
    min_score = float(gate.get("min_quality_score", 60.0))
    max_gap = int(gate.get("max_gap_bars", 12))
    cutoff = datetime.now(timezone.utc) - QUALITY_REPORT_MAX_AGE

    checks = []
    for symbol in symbols:
        report = (
            db.query(DataQualityReport)
            .filter(DataQualityReport.symbol == symbol, DataQualityReport.created_at >= cutoff)
            .order_by(DataQualityReport.created_at.desc())
            .first()
        )
        if report is None:
            # the Data Engine is optional - training datasets are fetched
            # live from Binance, so a missing report informs, not blocks
            checks.append(
                _check(
                    f"data_quality:{symbol}",
                    True,
                    "No recent quality report (Data Engine has not scanned this symbol in 48h) - "
                    "training data is fetched live and validated at build time",
                )
            )
            continue
        score_ok = report.quality_score is None or report.quality_score >= min_score
        gap_ok = (report.largest_gap_bars or 0) <= max_gap
        detail = (
            f"quality score {report.quality_score if report.quality_score is not None else 'n/a'} "
            f"(need >= {min_score}), largest gap {report.largest_gap_bars or 0} bars (max {max_gap}) "
            f"[{report.timeframe} report from {report.created_at:%Y-%m-%d %H:%M} UTC]"
        )
        checks.append(_check(f"data_quality:{symbol}", score_ok and gap_ok, detail))
    return checks


def _system_checks(gate: dict, db: Session) -> list[dict]:
    checks = []

    try:
        db.execute(text("SELECT 1"))
        checks.append(_check("database", True, "Database reachable and answering queries"))
    except Exception as exc:
        checks.append(_check("database", False, f"Database check failed: {exc!r}"))

    min_ram = float(gate.get("min_free_ram_mb", 400))
    available_mb = psutil.virtual_memory().available / 1e6
    checks.append(
        _check(
            "memory",
            available_mb >= min_ram,
            f"{available_mb:.0f}MB RAM free (need >= {min_ram:.0f}MB to train without starving the live backend)",
        )
    )

    min_disk = float(gate.get("min_free_disk_mb", 500))
    disk_path = MODELS_DISK_PATH if MODELS_DISK_PATH.exists() else Path("/")
    free_mb = shutil.disk_usage(disk_path).free / 1e6
    checks.append(
        _check(
            "disk",
            free_mb >= min_disk,
            f"{free_mb:.0f}MB disk free at {disk_path} (need >= {min_disk:.0f}MB for model artifacts)",
        )
    )
    return checks


def run_preflight(params: dict | None = None, db: Session | None = None) -> dict:
    """Full pre-training gate. `healthy` is the AND of every check."""
    from app.ml_lab.settings_repo import get_settings

    owns_session = db is None
    db = db or SessionLocal()
    try:
        gate = get_settings(db=db).get("training_gate", {})
        checks = _system_checks(gate, db)
        # only spend the dataset build if the host itself is usable
        host_ok = all(c["passed"] for c in checks)
        if host_ok:
            checks.extend(_dataset_checks(gate, params))
            checks.extend(_quality_checks(gate, params, db))
        else:
            checks.append(
                _check("historical_data", False, "Not evaluated - host health checks failed first")
            )

        failures = [c for c in checks if not c["passed"]]
        return {
            "healthy": len(failures) == 0,
            "checks": checks,
            "failures": [f"{c['name']}: {c['detail']}" for c in failures],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if owns_session:
            db.close()
