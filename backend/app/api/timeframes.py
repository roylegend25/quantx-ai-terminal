import asyncio
import statistics
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.intelligence import market_intelligence
from app.timeframes.multi_timeframe import evaluate_all
from app.trading_horizon.service import PROFILES, build_horizon_decision
from app.trading_horizon.authority import issue_horizon_authority
from app.db.models import PredictionLedger, PredictionResolution
from app.db.session import SessionLocal
from app.core.deps import get_current_user
from app.core.config import settings
from app.decision_engine.repository import get_setting, is_available, owner
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
from app.monitoring.logging import get_logger, log_event
from app.risk import settings_repository
from app.timeframes.canonical import parse_timeframe
from app.trading import modes

router = APIRouter(prefix="/api/timeframes", tags=["timeframes"])
logger = get_logger("quantx.trading_horizon")
_inflight_evaluations: dict[str, asyncio.Task] = {}


MIN_RESOLVED_EDGE_SAMPLES = 3


def _load_historical_performance(db, *, owner_id: str, symbol: str) -> tuple[list[dict], dict[str, dict[str, list[float]]]]:
    resolutions: list[dict] = []
    samples: dict[str, dict[str, list[float]]] = {}
    rows = (
        db.query(PredictionLedger, PredictionResolution)
        .join(PredictionResolution, PredictionResolution.prediction_id == PredictionLedger.prediction_id)
        .filter(PredictionLedger.user_id == owner_id, PredictionLedger.symbol == symbol,
                PredictionLedger.engine == "active_drive_v2",
                PredictionResolution.resolved_at > PredictionLedger.generated_at)
        .order_by(PredictionResolution.resolved_at.desc()).limit(25).all()
    )
    for prediction, resolution in reversed(rows):
        resolutions.append({"prediction_id": prediction.prediction_id, "direction": prediction.direction,
                            "outcome": "CORRECT" if resolution.correct else "INCORRECT" if resolution.correct is False else "NEUTRAL",
                            "actual_return": resolution.actual_return})
        timeframe_samples = samples.setdefault(prediction.timeframe, {
            "realized_returns": [], "forecast_expected_edges": [], "forecast_confidences": [],
        })
        # A zero or negative return is a real observation. Never use truthiness
        # here and never substitute a forecast for a missing realized outcome.
        if resolution.actual_return is not None:
            timeframe_samples["realized_returns"].append(float(resolution.actual_return))
        if prediction.expected_edge is not None:
            timeframe_samples["forecast_expected_edges"].append(float(prediction.expected_edge))
        if prediction.confidence is not None:
            timeframe_samples["forecast_confidences"].append(float(prediction.confidence))
    return resolutions, samples


def _historical_metrics(samples: dict[str, list[float]], *, owner_id: str, symbol: str,
                        timeframe: str) -> dict:
    realized = samples.get("realized_returns", [])
    forecasts = samples.get("forecast_expected_edges", [])
    confidences = samples.get("forecast_confidences", [])
    count = len(realized)
    supported = count >= MIN_RESOLVED_EDGE_SAMPLES
    positive = sum(value > 0 for value in realized)
    negative = sum(value < 0 for value in realized)
    flat = sum(value == 0 for value in realized)
    mean = sum(realized) / count if count else None
    historical_performance = {
        "metric_source": "realized_out_of_sample_returns",
        "resolved_sample_count": count,
        "realized_return_mean": mean,
        "realized_return_median": statistics.median(realized) if realized else None,
        "realized_edge_lower_bound": min(realized) if supported else None,
        "realized_edge_upper_bound": max(realized) if supported else None,
        "positive_count": positive, "negative_count": negative, "flat_count": flat,
        "realized_positive_rate": positive / count if count else None,
        "realized_negative_rate": negative / count if count else None,
        "realized_flat_rate": flat / count if count else None,
        "bounds_method": "observed_range",
        "minimum_samples": MIN_RESOLVED_EDGE_SAMPLES,
        "supported": supported,
        "reason": None if supported else "INSUFFICIENT_RESOLVED_SAMPLES",
        "scope": {"user_id": owner_id, "symbol": symbol, "timeframe": timeframe,
                  "engine": "active_drive_v2", "engine_version_policy": "all_persisted_active_drive_v2_versions",
                  "profile_policy": "all_profiles_ledger_does_not_persist_profile",
                  "resolution_ordering": "resolved_at_after_generated_at",
                  "resolved_out_of_sample_only": True},
    }
    forecast_calibration = {
        "metric_source": "persisted_prediction_time_values",
        "forecast_count": len(forecasts),
        "forecast_expected_edge_mean": sum(forecasts) / len(forecasts) if forecasts else None,
        "predicted_return_mean": sum(forecasts) / len(forecasts) if forecasts else None,
        "forecast_confidence_mean": sum(confidences) / len(confidences) if confidences else None,
    }
    return {
        "historical_performance": historical_performance,
        "forecast_calibration": forecast_calibration,
        # Deprecated compatibility fields; both historical values are now
        # derived exclusively from resolved returns.
        "historical_expected_edge": mean,
        "historical_edge_sample_count": count,
        "historical_edge_lower_bound": historical_performance["realized_edge_lower_bound"],
        "historical_edge_upper_bound": historical_performance["realized_edge_upper_bound"],
        "historical_edge_scope": historical_performance["scope"],
        "deprecated_fields": ["historical_expected_edge", "historical_edge_sample_count",
                              "historical_edge_lower_bound", "historical_edge_upper_bound"],
        "authoritative": False,
    }


def can_persist_horizon_authority(selected_engine: str | None, decision: dict,
                                  timeframe_decisions: dict[str, dict]) -> tuple[bool, str | None]:
    if selected_engine != "active_drive_v2":
        return False, "ACTIVE_DRIVE_V2_NOT_AUTHORITATIVE"
    required = set(decision["required_timeframes"]) | {decision["structural_bias_timeframe"]}
    for timeframe in required:
        item = timeframe_decisions.get(timeframe) or {}
        if not item.get("decision_id"):
            return False, "HORIZON_REQUIRED_DECISION_NOT_DURABLE"
        if item.get("engine") != "active_drive_v2" or not item.get("engine_version"):
            return False, "HORIZON_REQUIRED_DECISION_NOT_DURABLE"
        if item.get("symbol") != decision["symbol"] or item.get("timeframe") != timeframe:
            return False, "HORIZON_REQUIRED_DECISION_SCOPE_INVALID"
    return True, None


def _preview_contract(decision: dict, *, selected_engine: str | None, code: str, message: str) -> dict:
    decision.update({
        "selected_engine": selected_engine,
        "required_engine": "active_drive_v2",
        "preview_only": True,
        "persisted": False,
        "authority_status": "preview_blocked",
        "profile_decision_id": None,
        "horizon_decision_id": None,
        "direction": "NO_TRADE",
        "ready": False,
        "readiness": False,
        "execution_eligible": False,
        "blocking_reasons": [{"code": code, "message": message}],
    })
    if code not in decision["blockers"]:
        decision["blockers"].append(code)
    return decision


@router.get("/{symbol}")
async def timeframes(symbol: str):
    symbol = symbol.upper()

    try:
        market_context = await market_intelligence.get_context(symbol)
    except Exception:
        market_context = None

    result = await evaluate_all(symbol, market_context)

    return {
        "symbol": symbol,
        **result,
    }


async def _evaluate_horizon(symbol: str, chart_timeframe: str | None = None,
                          price: float | None = None, atr: float | None = None,
                          current_user: str = "admin", *, issue_authority: bool = False,
                          evaluation_reason: str = "dashboard_preview", account_id: str = "default"):
    """Calculate a preview and, only for the internal path, issue ready authority."""
    symbol = symbol.upper()
    evaluation_id = uuid.uuid4().hex
    evaluation_started = time.monotonic()
    evaluation_started_at = datetime.now(timezone.utc)
    if chart_timeframe is not None:
        try:
            chart_timeframe = parse_timeframe(chart_timeframe).value
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Required authority/profile state is loaded before optional history.
    owner_id = owner(current_user)
    state_db = SessionLocal()
    try:
        setting = get_setting(state_db, current_user)
        requested_profile = setting.trading_profile or "auto_adaptive"
        profile_revision = setting.profile_revision or 1
        strict_unanimity_required = setting.strict_timeframe_unanimity is not False
        selected_engine = setting.decision_engine
        risk_policy_revision = None
        if issue_authority:
            risk_state = settings_repository.get_user_settings(owner_id, scope="paper", db=state_db)  # Horizon issuance has no mode signal here; conservative default preserves pre-split behavior
            risk_policy_revision = risk_state.get("updated_at") or "risk-settings:initial"
    finally:
        state_db.close()
    required_engine = "active_drive_v2"
    engine = decision_engine_router.engines[DecisionEngineType.ACTIVE_DRIVE_V2]
    active_drive_v2_available = is_available(DecisionEngineType.ACTIVE_DRIVE_V2)
    if requested_profile == "auto_adaptive":
        profile_timeframe_configuration = {
            key: {"execution": value.execution_timeframe, "required": list(value.required_timeframes),
                  "bias": value.structural_bias_timeframe} for key, value in PROFILES.items()
        }
    else:
        if requested_profile not in PROFILES:
            raise HTTPException(status_code=422, detail=f"Unknown Trading Horizon profile: {requested_profile}")
        configured = PROFILES[requested_profile]
        profile_timeframe_configuration = {requested_profile: {
            "execution": configured.execution_timeframe, "required": list(configured.required_timeframes),
            "bias": configured.structural_bias_timeframe,
        }}

    resolutions: list[dict] = []
    historical_edge_values: dict[str, list[float]] = {}
    historical_data_available = False
    historical_data_error_code = None
    degraded_reasons: list[dict] = []
    history_db = SessionLocal()
    try:
        resolutions, historical_edge_values = _load_historical_performance(
            history_db, owner_id=owner_id, symbol=symbol)
        historical_data_available = True
    except Exception as exc:
        historical_data_error_code = "HISTORICAL_LEDGER_UNAVAILABLE"
        degraded_reasons.append({"code": historical_data_error_code,
            "message": "Historical comparison data is temporarily unavailable."})
        log_event(logger, message="trading_horizon_history_unavailable", category="trading_horizon",
                  user_id=owner_id, symbol=symbol, error_type=type(exc).__name__)
    finally:
        history_db.close()
    # Build the matrix from authoritative Active Drive V2 predictions, not
    # the legacy ensemble used by the generic timeframe diagnostics route.
    from app.api.prediction import prediction as active_drive_prediction
    if requested_profile == "auto_adaptive":
        evaluation_timeframes = {tf for item in PROFILES.values() for tf in (*item.required_timeframes, item.structural_bias_timeframe)}
    else:
        selected = PROFILES[requested_profile]
        evaluation_timeframes = {*selected.required_timeframes, selected.structural_bias_timeframe}
    if chart_timeframe:
        evaluation_timeframes.add(chart_timeframe)
    authoritative_timeframes = {}
    prediction_calls = 0
    newly_calculated = 0
    timeout_count = 0
    timeframe_durations: dict[str, int] = {}
    if selected_engine == "active_drive_v2" and active_drive_v2_available:
        semaphore = asyncio.Semaphore(max(1, settings.horizon_evaluation_concurrency))

        async def evaluate_timeframe(timeframe: str) -> tuple[str, dict]:
            nonlocal prediction_calls, newly_calculated, timeout_count
            started = time.monotonic()
            try:
                async with semaphore:
                    prediction_calls += 1
                    response = await asyncio.wait_for(
                        active_drive_prediction(symbol, timeframe=timeframe, current_user=current_user),
                        timeout=settings.horizon_timeframe_timeout_seconds,
                    )
                result = dict(response["prediction"]["decision_engine"])
                newly_calculated += 1
                result["evaluation_id"] = evaluation_id
                result["evaluation_source"] = "newly_calculated_decision"
                return timeframe, result
            except asyncio.TimeoutError:
                timeout_count += 1
                return timeframe, {"timeframe": timeframe, "direction": "NO_TRADE",
                    "eligible_for_execution": False, "error": "TIMEFRAME_EVALUATION_TIMEOUT",
                    "evaluation_id": evaluation_id}
            except Exception as exc:
                log_event(logger, message="horizon_timeframe_evaluation_failed", category="trading_horizon",
                          evaluation_id=evaluation_id, symbol=symbol, timeframe=timeframe,
                          error_type=type(exc).__name__)
                return timeframe, {"timeframe": timeframe, "direction": "NO_TRADE",
                    "eligible_for_execution": False, "error": "TIMEFRAME_DECISION_FAILED",
                    "evaluation_id": evaluation_id}
            finally:
                timeframe_durations[timeframe] = int((time.monotonic() - started) * 1000)

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(evaluate_timeframe(tf) for tf in sorted(evaluation_timeframes))),
                timeout=settings.horizon_evaluation_timeout_seconds,
            )
            authoritative_timeframes = dict(results)
        except asyncio.TimeoutError:
            timeout_count += 1
            authoritative_timeframes = {tf: {"timeframe": tf, "direction": "NO_TRADE",
                "eligible_for_execution": False, "error": "HORIZON_EVALUATION_DEADLINE_EXCEEDED",
                "evaluation_id": evaluation_id} for tf in evaluation_timeframes}
    else:
        authoritative_timeframes = {timeframe: {"direction": "NO_TRADE", "eligible_for_execution": False,
            "expected_edge": None, "error": "ACTIVE_DRIVE_V2_NOT_AUTHORITATIVE",
            "evaluation_id": evaluation_id} for timeframe in evaluation_timeframes}
    try:
        # Soft multi-timeframe confirmation is a PAPER-mode-only policy
        # (see app.trading_horizon.service.build_horizon_decision): any
        # other effective mode (including a locked/kill-switched state)
        # keeps the original strict-unanimity policy unchanged.
        soft_confirmation = modes.effective_mode() == modes.MODE_PAPER
        decision = build_horizon_decision(symbol, authoritative_timeframes, requested_profile, price=price, atr=atr,
                    resolutions=resolutions, user_id=owner_id, engine_id="active_drive_v2", engine_version=engine.version,
                    soft_confirmation=soft_confirmation)
        edge_samples = historical_edge_values.get(decision["execution_timeframe"], {})
        decision["historical_edge_summary"] = _historical_metrics(
            edge_samples, owner_id=owner_id, symbol=symbol, timeframe=decision["execution_timeframe"])
        decision.update({
            "evaluation_id": evaluation_id,
            "evaluation_context": {
                "evaluation_id": evaluation_id, "user_id": owner_id, "account_id": account_id,
                "symbol": symbol, "selected_profile": requested_profile,
                "profile_revision": profile_revision, "risk_policy_revision": risk_policy_revision,
                "engine_id": "active_drive_v2",
                "engine_version": engine.version, "evaluation_started_at": evaluation_started_at.isoformat(),
                "evaluation_deadline": settings.horizon_evaluation_timeout_seconds,
                "reason": evaluation_reason,
                "required_timeframe_roles": {
                    tf: ("execution" if tf == decision["execution_timeframe"] else
                         "bias" if tf == decision["structural_bias_timeframe"] else "confirmation")
                    for tf in set(decision["required_timeframes"]) | {decision["structural_bias_timeframe"]}
                },
                "market_data_revision": "|".join(
                    f"{tf}:{authoritative_timeframes[tf].get('market_data_revision', 'unknown')}"
                    for tf in sorted(authoritative_timeframes)),
                "performance_snapshot_revision": "|".join(
                    f"{tf}:{authoritative_timeframes[tf].get('performance_snapshot_revision', 'unknown')}"
                    for tf in sorted(authoritative_timeframes)),
            },
            "evaluation_duration_ms": int((time.monotonic() - evaluation_started) * 1000),
            "timeframe_count": len(evaluation_timeframes),
            "prediction_calls": prediction_calls,
            "reused_decisions": 0,
            "newly_calculated_decisions": newly_calculated,
            "timeout_count": timeout_count,
            "per_timeframe_duration_ms": timeframe_durations,
            "authority_issued": False,
            "active_engine": selected_engine,
            "selected_engine": selected_engine,
            "required_engine": required_engine,
            "profile_revision": profile_revision,
            "strict_unanimity_required": strict_unanimity_required,
            "profile_timeframe_configuration": profile_timeframe_configuration,
            "historical_data_available": historical_data_available,
            "historical_data_error_code": historical_data_error_code,
            "degraded": not historical_data_available,
            "degraded_reasons": degraded_reasons,
        })
        decision["chart_timeframe"] = chart_timeframe
        decision["previous_resolved_count"] = len(resolutions)
        evaluation_errors = [item.get("error") for item in authoritative_timeframes.values() if item.get("error")]
        if evaluation_errors and selected_engine == "active_drive_v2":
            code = ("HORIZON_EVALUATION_DEADLINE_EXCEEDED"
                    if "HORIZON_EVALUATION_DEADLINE_EXCEEDED" in evaluation_errors else evaluation_errors[0])
            return _preview_contract(decision, selected_engine=selected_engine, code=code,
                message="A required timeframe did not complete within the Horizon evaluation budget.")
        can_persist, persistence_blocker = can_persist_horizon_authority(selected_engine, decision, authoritative_timeframes)
        if not historical_data_available and can_persist:
            can_persist, persistence_blocker = False, "HISTORICAL_LEDGER_UNAVAILABLE"
        if not can_persist:
            message = ("Active Drive V2 must be selected before a durable Trading Horizon authority can be created."
                       if persistence_blocker == "ACTIVE_DRIVE_V2_NOT_AUTHORITATIVE"
                       else "Historical comparison data is temporarily unavailable."
                       if persistence_blocker == "HISTORICAL_LEDGER_UNAVAILABLE"
                       else "Every required timeframe must have a durable, correctly scoped Active Drive V2 decision.")
            return _preview_contract(decision, selected_engine=selected_engine,
                                     code=persistence_blocker or "HORIZON_AUTHORITY_NOT_PERSISTABLE", message=message)
        decision["readiness"] = decision["ready"]
        decision["execution_eligible"] = bool(decision["ready"] and decision["direction"] in {"LONG", "SHORT"})
        decision["blocking_reasons"] = [{"code": blocker, "message": blocker} for blocker in decision["blockers"]]

        # A public preview is deliberately incapable of minting entry authority.
        # Even a fully eligible preview remains non-executable until the internal
        # scheduler explicitly enters the issuance path.
        if not issue_authority:
            preview_ready = decision["execution_eligible"]
            decision.update({
                "authority_status": "preview_ready" if preview_ready else "preview_blocked",
                "preview_execution_eligible": preview_ready,
                "execution_eligible": False,
                "persisted": False,
                "preview_only": True,
                "profile_decision_id": None,
                "horizon_decision_id": None,
                "authoritative_execution_decision_id": None,
            })
            return decision

        if (not decision["ready"] or not decision["execution_eligible"]
                or decision["direction"] not in {"LONG", "SHORT"}
                or decision["blockers"] or decision["degraded"]
                or not decision["current_edge_supported"]
                or not decision["strict_unanimity_required"]
                or not decision["unanimity_passed"]):
            return _preview_contract(decision, selected_engine=selected_engine,
                code="HORIZON_AUTHORITY_NOT_ELIGIBLE",
                message="Trading Horizon mandatory issuance gates did not pass.")
        persistence_db = SessionLocal()
        try:
            persisted = issue_horizon_authority(persistence_db, user_id=current_user, policy=decision,
                timeframe_decisions=authoritative_timeframes, profile_revision=profile_revision)
            decision["profile_decision_id"] = persisted.id
            decision["horizon_decision_id"] = persisted.id
            decision["persisted"] = True
            decision["preview_only"] = False
            decision["authority_status"] = "persisted_authority"
            decision["authority_issued"] = True
            snapshot = (persisted.evidence or {}).get("execution_snapshot") or {}
            decision["authoritative_execution_decision_id"] = snapshot.get("authoritative_decision_id")
            decision["bound_execution_decision"] = {
                "decision_id": snapshot.get("authoritative_decision_id"),
                "timeframe": snapshot.get("execution_timeframe"),
                "direction": snapshot.get("direction"),
                "generated_at": snapshot.get("generated_at"),
                "expires_at": snapshot.get("expires_at"),
                "entry_reference": snapshot.get("decision_price"),
                "stop": snapshot.get("risk", {}).get("stop_price"),
                "target": snapshot.get("risk", {}).get("target_price"),
                "expected_holding_window": snapshot.get("holding"),
            }
            risk_approval = snapshot.get("risk_approval") or {}
            decision["position_sizing"] = {
                "configured_user_limit_usd": risk_approval.get("configured_max_position_size_usd"),
                "persisted_authority_limit_usd": risk_approval.get("approved_notional_ceiling_usd"),
                "risk_based_limit_usd": risk_approval.get("risk_based_notional_usd"),
                "approved_risk_budget_usd": risk_approval.get("approved_risk_budget_usd"),
                "authoritative": True,
                "client_overridable": False,
            }
        except Exception as exc:
            return _preview_contract(decision, selected_engine=selected_engine,
                code="HORIZON_AUTHORITY_PERSISTENCE_FAILED",
                message=f"Durable Trading Horizon authority was not created ({type(exc).__name__}).")
        finally:
            persistence_db.close()
        return decision
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{symbol}/horizon")
async def trading_horizon(symbol: str, chart_timeframe: str | None = None,
                          price: float | None = None, atr: float | None = None,
                          current_user: str = Depends(get_current_user)):
    """Side-effect-free Trading Horizon preview; never creates authority."""
    return await _evaluate_horizon(symbol, chart_timeframe, price, atr, current_user,
                                   issue_authority=False)


async def evaluate_and_issue_horizon_authority(*, user_id: str, account_id: str,
                                                symbol: str, evaluation_reason: str,
                                                idempotency_key: str | None = None) -> dict:
    """One in-process evaluation whose exact results are passed to issuance."""
    from app.db.init_db import check_schema_compatibility
    if not check_schema_compatibility()["compatible"]:
        return {"authority_status": "preview_blocked", "persisted": False,
                "horizon_decision_id": None, "ready": False, "execution_eligible": False,
                "direction": "NO_TRADE", "blockers": ["TRADING_HORIZON_MIGRATION_REQUIRED"],
                "blocking_reasons": [{"code": "TRADING_HORIZON_MIGRATION_REQUIRED",
                    "message": "Trading Horizon schema upgrade is required."}]}
    async def evaluate() -> dict:
        return await _evaluate_horizon(symbol, current_user=user_id, issue_authority=True,
                                       evaluation_reason=evaluation_reason, account_id=account_id)
    if not idempotency_key:
        return await evaluate()
    key = f"{owner(user_id)}:{account_id}:{symbol.upper()}:{idempotency_key}"
    task = _inflight_evaluations.get(key)
    if task is None:
        if len(_inflight_evaluations) >= 256:
            completed = [item for item, value in _inflight_evaluations.items() if value.done()]
            for item in completed[:128]:
                _inflight_evaluations.pop(item, None)
        task = asyncio.create_task(evaluate())
        _inflight_evaluations[key] = task
    return await asyncio.shield(task)
