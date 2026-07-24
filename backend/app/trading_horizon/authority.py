"""ARCHIVED (main-purpose consolidation, Stage 2): Persistence and
server-side verification of immutable entry authority.

Confirmed unreachable from production - superseded by
app.decision_engine.execution_gate's single-authoritative-decision model.
Zero importers outside this package and its own tests
(tests/test_horizon_authority.py); tests/test_horizon_not_invoked_in_production.py
statically enforces that no production module may import this module or
app.trading_horizon.service. Kept in place (not deleted) for historical
reference and because trading_horizon_decisions/_consumptions/
_timeframe_links still hold real historical rows this module wrote before
being superseded - do not delete those tables' data as part of any cleanup.
"""
from __future__ import annotations

import uuid
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    ActiveDriveDecision,
    Portfolio,
    Trade,
    TradingHorizonDecision,
    TradingHorizonConsumption,
    TradingHorizonTimeframeLink,
)
from app.risk import settings_repository
from app.trading import risk_manager
from app.decision_engine.repository import get_setting, owner
from app.decision_engine.router import decision_engine_router
from app.decision_engine.types import DecisionEngineType
from app.trading_horizon.service import PROFILES


class HorizonAuthorityError(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parsed(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return fallback


def _first_not_none(*values):
    return next((value for value in values if value is not None), None)


def _execution_snapshot(decision_id: str, user_id: str, symbol: str, timeframe: str,
                        payload: dict, generated_at: datetime, expires_at: datetime,
                        risk_approval: dict) -> dict:
    direction = str(payload.get("final_signal") or payload.get("signal") or "NO_TRADE").upper()
    reference_price = payload.get("reference_price")
    stop = payload.get("recommended_stop")
    target = payload.get("recommended_target")
    horizon_seconds = int(payload.get("target_horizon_seconds") or 0)
    confidence = _first_not_none(payload.get("confidence"), payload.get("decision_confidence"))
    confidence_pct = confidence * 100 if isinstance(confidence, (int, float)) and confidence <= 1 else confidence
    return {
        "authoritative_decision_id": decision_id,
        "user_id": user_id,
        "symbol": symbol,
        "execution_timeframe": timeframe,
        "direction": direction,
        "decision_price": reference_price,
        "entry_policy": {"type": "market", "reference_price": reference_price,
                         "maximum_slippage_bps": 50},
        "risk": {"stop_price": stop, "target_price": target,
                 "invalidation_price": stop, "risk_reward_ratio": payload.get("risk_reward_ratio"),
                 "maximum_position_risk": payload.get("maximum_position_risk")},
        "holding": {"minimum_seconds": 0, "most_likely_seconds": horizon_seconds,
                    "maximum_seconds": horizon_seconds, "thesis_expires_at": expires_at.isoformat()},
        "evidence": {
            "directional_confidence": confidence_pct,
            "expected_edge": payload.get("expected_edge"),
            "point_margin": payload.get("point_margin"),
            "total_evidence": payload.get("total_evidence"),
            "market_regime": payload.get("market_regime"),
            "market_data_revision": str(payload.get("market_data_revision") or "unknown"),
            "performance_snapshot_revision": str(payload.get("performance_snapshot_revision") or "unknown"),
            "feature_snapshot_hash": payload.get("feature_snapshot_hash"),
        },
        "generated_at": generated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "risk_approval": risk_approval,
    }


def _portfolio_risk_gate(db: Session, *, user_id: str, direction: str, confidence_pct: float,
                         open_positions: int, balance: float, daily_pnl: float) -> None:
    """The daily/weekly loss, drawdown, consecutive-loss and cooldown
    breakers configured on the Risk Management page
    (app.trading.risk_manager.evaluate_risk) were never actually wired into
    the automated V2 path - they gated nothing. Authority issuance is the
    single choke point every automated new-entry passes through, so this is
    where those breakers now apply; a blocked trade here is a correct
    outcome, not a bug, whenever a configured limit is genuinely tripped."""
    closed_trades = (db.query(Trade).filter(Trade.status == "CLOSED", Trade.user_id == user_id)
                     .order_by(Trade.closed_at.desc()).limit(200).all())
    trade_history = [{"status": t.status, "pnl": t.pnl,
                      "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                      "closed_at": t.closed_at.isoformat() if t.closed_at else None} for t in closed_trades]
    verdict = risk_manager.evaluate_risk(
        confidence=confidence_pct, direction=direction, open_positions=open_positions,
        portfolio={"balance": balance, "daily_pnl": daily_pnl}, trade_history=trade_history,
    )
    if not verdict["allowed"]:
        raise HorizonAuthorityError(f"PORTFOLIO_RISK_LIMIT_BLOCKED:{verdict['reason']}")


def _risk_approval_snapshot(db: Session, *, user_id: str, symbol: str, decision_id: str,
                            profile: str, timeframe: str, payload: dict,
                            generated_at: datetime, expires_at: datetime,
                            direction: str, confidence_pct: float) -> dict:
    risk = settings_repository.get_user_settings(user_id, scope="paper", db=db)  # Horizon authority issuance has no mode signal today; conservative default preserves pre-split behavior
    configured = float(risk["max_position_size_usd"])
    risk_pct = float(risk["max_risk_per_trade_pct"])
    if configured <= 0 or risk_pct <= 0:
        raise HorizonAuthorityError("POSITION_SIZE_LIMIT_ZERO")
    portfolio = db.get(Portfolio, 1)
    equity = float((portfolio.equity if portfolio and portfolio.equity is not None else
                    portfolio.balance if portfolio and portfolio.balance is not None else 10_000.0))
    balance = float(portfolio.balance if portfolio and portfolio.balance is not None else equity)
    daily_pnl = float(portfolio.daily_pnl if portfolio and portfolio.daily_pnl is not None else 0.0)
    open_trades = db.query(Trade).filter(Trade.status == "OPEN", Trade.user_id == user_id).all()
    _portfolio_risk_gate(db, user_id=user_id, direction=direction, confidence_pct=confidence_pct,
                         open_positions=len(open_trades), balance=balance, daily_pnl=daily_pnl)
    portfolio_exposure = sum(abs(float(trade.entry or 0) * float(trade.qty or 0)) for trade in open_trades)
    symbol_exposure = sum(abs(float(trade.entry or 0) * float(trade.qty or 0))
                          for trade in open_trades if trade.symbol == symbol)
    decision_price = payload.get("reference_price")
    stop_price = payload.get("recommended_stop")
    stop_distance_pct = (abs(float(decision_price) - float(stop_price)) / float(decision_price) * 100
                         if isinstance(decision_price, (int, float)) and decision_price > 0
                         and isinstance(stop_price, (int, float)) else None)
    approved_risk_budget = equity * risk_pct / 100
    stop_based = (approved_risk_budget / (stop_distance_pct / 100)
                  if stop_distance_pct and stop_distance_pct > 0 else configured)
    portfolio_remaining = max(0.0, configured * int(risk.get("max_open_positions") or 1) - portfolio_exposure)
    symbol_remaining = max(0.0, configured - symbol_exposure)
    approved = min(configured, stop_based, portfolio_remaining, symbol_remaining, max(0.0, equity))
    if approved <= 0:
        raise HorizonAuthorityError("POSITION_SIZE_LIMIT_ZERO")
    revision = risk.get("updated_at") or "risk-settings:initial"
    return {
        "risk_policy_revision": revision,
        "risk_policy_owner": user_id,
        "account_connection_id": "default",
        "horizon_decision_id": None,
        "authoritative_execution_decision_id": decision_id,
        "user_id": user_id,
        "symbol": symbol,
        "trading_profile": profile,
        "execution_timeframe": timeframe,
        "configured_max_position_size_usd": configured,
        "configured_risk_per_trade": risk_pct,
        "approved_risk_budget_usd": approved_risk_budget,
        "approved_notional_ceiling_usd": approved,
        "risk_based_notional_usd": stop_based,
        "decision_price": decision_price,
        "stop_price": stop_price,
        "stop_distance_pct": stop_distance_pct,
        "maximum_leverage": 1.0,
        "portfolio_exposure_at_decision": portfolio_exposure,
        "symbol_exposure_at_decision": symbol_exposure,
        "generated_at": generated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def issue_horizon_authority(
    db: Session,
    *,
    user_id: str,
    policy: dict,
    timeframe_decisions: dict[str, dict],
    profile_revision: int,
) -> TradingHorizonDecision:
    """Atomically issue an immutable authority after every mandatory gate."""
    try:
        from app.db.init_db import check_schema_compatibility
        if not check_schema_compatibility(db.get_bind())["compatible"]:
            raise HorizonAuthorityError("TRADING_HORIZON_MIGRATION_REQUIRED")
        if (policy.get("ready") is not True
                or policy.get("direction") not in {"LONG", "SHORT"}
                or policy.get("blockers")
                or policy.get("degraded") is True
                or policy.get("current_edge_supported") is not True
                or policy.get("strict_unanimity_required") is not True
                or policy.get("unanimity_passed") is not True):
            raise HorizonAuthorityError("HORIZON_AUTHORITY_NOT_ELIGIBLE")
        now = datetime.now(timezone.utc)
        decision_id = uuid.uuid4().hex
        execution_tf = policy["execution_timeframe"]
        required = set(policy["required_timeframes"]) | {policy["structural_bias_timeframe"]}
        if execution_tf not in required or any(timeframe not in timeframe_decisions for timeframe in required):
            raise HorizonAuthorityError("HORIZON_REQUIRED_DECISION_MISSING")
        normalized_user = owner(user_id)
        if policy.get("evaluation_id") and any(
            timeframe_decisions[timeframe].get("evaluation_id") != policy["evaluation_id"]
            for timeframe in required
        ):
            raise HorizonAuthorityError("HORIZON_EVALUATION_CONTEXT_MISMATCH")
        for timeframe in required:
            item = timeframe_decisions[timeframe]
            if not all(item.get(field) for field in ("decision_id", "engine", "engine_version")):
                raise HorizonAuthorityError("HORIZON_REQUIRED_DECISION_NOT_DURABLE")
            persisted = db.get(ActiveDriveDecision, item["decision_id"])
            payload = persisted.decision_payload if persisted else {}
            if (persisted is None or persisted.user_id != normalized_user or persisted.symbol != policy["symbol"].upper()
                    or persisted.timeframe != timeframe or persisted.engine != "active_drive_v2" or persisted.shadow
                    or item["engine"] != "active_drive_v2" or persisted.engine_version != item["engine_version"]):
                raise HorizonAuthorityError("HORIZON_REQUIRED_DECISION_NOT_DURABLE")
            if timeframe == execution_tf and (
                payload.get("expected_edge") != item.get("expected_edge")
                or bool(payload.get("current_edge_supported", payload.get("edge_supported", False)))
                   != bool(item.get("current_edge_supported", item.get("edge_supported", False)))
            ):
                raise HorizonAuthorityError("HORIZON_CURRENT_EDGE_IDENTITY_MISMATCH")
        authority = timeframe_decisions[execution_tf]
        authority_confidence = _first_not_none(authority.get("confidence"), authority.get("decision_confidence"))
        if authority_confidence is None:
            raise HorizonAuthorityError("HORIZON_AUTHORITY_NOT_ELIGIBLE")
        if not policy.get("generated_at") or not policy.get("expires_at"):
            raise HorizonAuthorityError("HORIZON_AUTHORITY_NOT_ELIGIBLE")
        generated_at = _parsed(policy.get("generated_at"), now)
        expires_at = _parsed(policy.get("expires_at"), generated_at)
        if expires_at <= generated_at or expires_at <= now:
            raise HorizonAuthorityError("HORIZON_AUTHORITY_NOT_ELIGIBLE")
        authoritative_persisted = db.get(ActiveDriveDecision, authority["decision_id"])
        authoritative_payload = authoritative_persisted.decision_payload or {}
        confidence_pct = authority_confidence * 100 if authority_confidence <= 1 else authority_confidence
        risk_approval = _risk_approval_snapshot(db, user_id=normalized_user, symbol=policy["symbol"].upper(),
            decision_id=authority["decision_id"], profile=policy["resolved_profile"], timeframe=execution_tf,
            payload=authoritative_payload, generated_at=generated_at, expires_at=expires_at,
            direction=policy["direction"], confidence_pct=confidence_pct)
        context_risk_revision = (policy.get("evaluation_context") or {}).get("risk_policy_revision")
        if context_risk_revision and context_risk_revision != risk_approval["risk_policy_revision"]:
            raise HorizonAuthorityError("HORIZON_EVALUATION_CONTEXT_MISMATCH")
        fingerprint_payload = {
            "user_id": normalized_user, "symbol": policy["symbol"].upper(),
            "profile_revision": profile_revision, "risk_policy_revision": risk_approval["risk_policy_revision"],
            "decision_ids": sorted(timeframe_decisions[timeframe]["decision_id"] for timeframe in required),
            "engine_version": authority["engine_version"],
            "market_data_revision": str(authority.get("market_data_revision") or "unknown"),
            "performance_snapshot_revision": str(authority.get("performance_snapshot_revision") or "unknown"),
        }
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
        existing = db.query(TradingHorizonDecision).filter_by(issuance_fingerprint=fingerprint).first()
        if existing is not None:
            policy["profile_decision_id"] = existing.id
            return existing
        risk_approval["horizon_decision_id"] = decision_id
        snapshot = _execution_snapshot(authority["decision_id"], normalized_user, policy["symbol"].upper(),
                                       execution_tf, authoritative_payload, generated_at, expires_at, risk_approval)
        record = TradingHorizonDecision(
        id=decision_id,
        issuance_fingerprint=fingerprint,
        user_id=owner(user_id),
        symbol=policy["symbol"].upper(),
        selected_profile=policy["resolved_profile"],
        profile_revision=profile_revision,
        authoritative_execution_timeframe=execution_tf,
        final_direction=policy["direction"],
        engine_id="active_drive_v2",
        engine_version=authority["engine_version"],
        unanimous=bool(policy["unanimity_passed"]),
        readiness=bool(policy["ready"]),
        execution_eligible=bool(policy["ready"] and policy["direction"] in {"LONG", "SHORT"}),
        generated_at=generated_at,
        expires_at=expires_at,
        market_data_revision=str(authority.get("market_data_revision") or "unknown"),
        performance_snapshot_revision=str(authority.get("performance_snapshot_revision") or "unknown"),
        expected_edge=policy.get("expected_edge"),
        directional_confidence=_first_not_none(authority.get("confidence"), authority.get("decision_confidence")),
        point_margin=abs(float(_first_not_none(authority.get("long_points"), 0))
                         - float(_first_not_none(authority.get("short_points"), 0))),
        evidence={"agreement_matrix": policy["agreement_matrix"], "selection_reason": policy["selection_reason"],
                  "evaluation_context": policy.get("evaluation_context"),
                  "execution_snapshot": snapshot},
        blocking_reasons=policy["blockers"],
        created_at=now,
        )
        db.add(record)
        db.flush()
        for timeframe in sorted(required):
            item = timeframe_decisions[timeframe]
            role = "execution" if timeframe == execution_tf else "bias" if timeframe == policy["structural_bias_timeframe"] else "confirmation"
            link_generated = _parsed(item.get("generated_at"), generated_at)
            link_expires = _parsed(item.get("expires_at"), expires_at)
            db.add(TradingHorizonTimeframeLink(
            horizon_decision_id=decision_id,
            decision_id=item["decision_id"],
            timeframe=timeframe,
            role=role,
            direction=str(item.get("final_signal") or item.get("direction") or "NO_TRADE").upper(),
            confidence=_first_not_none(item.get("confidence"), item.get("decision_confidence")),
            eligible=bool(item.get("eligible_for_execution")),
            engine_id=item["engine"],
            engine_version=item["engine_version"],
            generated_at=link_generated,
            expires_at=link_expires,
            ))
            db.flush()
        db.commit()
        policy["profile_decision_id"] = decision_id
        return record
    except IntegrityError:
        db.rollback()
        existing = db.query(TradingHorizonDecision).filter_by(issuance_fingerprint=locals().get("fingerprint")).first()
        if existing is not None:
            policy["profile_decision_id"] = existing.id
            return existing
        raise
    except Exception:
        db.rollback()
        raise


def persist_horizon_decision(db: Session, **kwargs) -> TradingHorizonDecision:
    """Compatibility alias for internal callers; applies the same issuance gates."""
    return issue_horizon_authority(db, **kwargs)


def validate_horizon_decision(
    db: Session,
    *,
    horizon_decision_id: str,
    user_id: str,
    symbol: str,
    direction: str | None = None,
    now: datetime | None = None,
    consume_key: str | None = None,
) -> TradingHorizonDecision:
    from app.db.init_db import check_schema_compatibility
    if not check_schema_compatibility(db.get_bind())["compatible"]:
        raise HorizonAuthorityError("TRADING_HORIZON_MIGRATION_REQUIRED")
    now = now or datetime.now(timezone.utc)
    row = db.get(TradingHorizonDecision, horizon_decision_id)
    if row is None:
        raise HorizonAuthorityError("HORIZON_DECISION_NOT_FOUND")
    if row.user_id != owner(user_id):
        raise HorizonAuthorityError("HORIZON_DECISION_USER_MISMATCH")
    if row.symbol != symbol.upper():
        raise HorizonAuthorityError("HORIZON_DECISION_SYMBOL_MISMATCH")
    if direction is not None and row.final_direction != direction.upper():
        raise HorizonAuthorityError("HORIZON_DECISION_DIRECTION_MISMATCH")
    setting = get_setting(db, user_id)
    if setting.decision_engine != "active_drive_v2" or row.engine_id != "active_drive_v2":
        raise HorizonAuthorityError("HORIZON_ENGINE_NOT_AUTHORITATIVE")
    current_engine = decision_engine_router.engines[DecisionEngineType.ACTIVE_DRIVE_V2]
    if row.engine_version != current_engine.version:
        raise HorizonAuthorityError("HORIZON_ENGINE_NOT_AUTHORITATIVE")
    if (setting.trading_profile or "auto_adaptive") not in {row.selected_profile, "auto_adaptive"} or (setting.profile_revision or 1) != row.profile_revision:
        raise HorizonAuthorityError("HORIZON_PROFILE_REVISION_MISMATCH")
    if not row.readiness:
        raise HorizonAuthorityError("HORIZON_DECISION_NOT_READY")
    if not row.execution_eligible:
        raise HorizonAuthorityError("HORIZON_DECISION_NOT_EXECUTION_ELIGIBLE")
    if row.final_direction not in {"LONG", "SHORT"}:
        raise HorizonAuthorityError("HORIZON_DECISION_NOT_EXECUTION_ELIGIBLE")
    if _aware(row.generated_at) > now or now >= _aware(row.expires_at):
        raise HorizonAuthorityError("HORIZON_DECISION_EXPIRED")
    if db.get(TradingHorizonConsumption, row.id) is not None:
        raise HorizonAuthorityError("HORIZON_DECISION_ALREADY_CONSUMED")
    profile = PROFILES.get(row.selected_profile)
    if profile is None or profile.execution_timeframe != row.authoritative_execution_timeframe:
        raise HorizonAuthorityError("HORIZON_TIMEFRAME_LINK_INVALID")
    links = db.query(TradingHorizonTimeframeLink).filter_by(horizon_decision_id=row.id).all()
    by_tf = {link.timeframe: link for link in links}
    expected = set(profile.required_timeframes) | {profile.structural_bias_timeframe}
    execution_links = [link for link in links if link.role == "execution"]
    if len(execution_links) != 1:
        raise HorizonAuthorityError("HORIZON_EXECUTION_DECISION_MISSING")
    if set(by_tf) != expected:
        raise HorizonAuthorityError("HORIZON_TIMEFRAME_LINK_INVALID")
    execution_decision = None
    for timeframe in expected:
        link = by_tf[timeframe]
        persisted = db.get(ActiveDriveDecision, link.decision_id)
        expected_role = "execution" if timeframe == profile.execution_timeframe else "bias" if timeframe == profile.structural_bias_timeframe else "confirmation"
        if (
            persisted is None or persisted.user_id != row.user_id or persisted.symbol != row.symbol
            or persisted.timeframe != timeframe or persisted.engine != "active_drive_v2" or persisted.shadow
            or link.engine_id != "active_drive_v2" or link.engine_version != row.engine_version
            or link.role != expected_role or persisted.signal != link.direction
            or bool(persisted.eligible_for_execution) != bool(link.eligible)
            or _aware(link.generated_at) > now or now >= _aware(link.expires_at)
        ):
            raise HorizonAuthorityError("HORIZON_EXECUTION_DECISION_MISMATCH" if expected_role == "execution"
                                        else "HORIZON_TIMEFRAME_LINK_INVALID")
        payload = persisted.decision_payload or {}
        if str(payload.get("market_data_revision") or "unknown") != row.market_data_revision and timeframe == profile.execution_timeframe:
            raise HorizonAuthorityError("HORIZON_EXECUTION_DECISION_MISMATCH")
        if str(payload.get("performance_snapshot_revision") or "unknown") != row.performance_snapshot_revision and timeframe == profile.execution_timeframe:
            raise HorizonAuthorityError("HORIZON_EXECUTION_DECISION_MISMATCH")
        if timeframe == profile.execution_timeframe and (
            payload.get("expected_edge") != row.expected_edge
            or not bool(payload.get("current_edge_supported", payload.get("edge_supported", False)))
        ):
            raise HorizonAuthorityError("HORIZON_TIMEFRAME_LINK_INVALID")
        # Only the execution/primary timeframe's link is required to match
        # final_direction and be independently eligible. Under the strict
        # (non-PAPER) policy every required timeframe was already forced
        # unanimous before build_horizon_decision ever set ready=True, so
        # this was previously an always-true assertion for confirmation
        # timeframes too - harmless. Under the PAPER-only soft confirmation
        # policy (app.trading_horizon.service._confirm_higher_timeframes) a
        # confirmation timeframe can legitimately be NEUTRAL (NO_TRADE,
        # eligible=False) while the decision still proceeds on the primary's
        # own evidence; re-imposing strict unanimity here would silently
        # reject every soft-confirmed decision at consumption time even
        # though it was correctly approved at issuance time.
        if timeframe == profile.execution_timeframe and (link.direction != row.final_direction or not link.eligible):
            raise HorizonAuthorityError("HORIZON_TIMEFRAME_LINK_INVALID")
        if expected_role == "bias" and link.direction not in {row.final_direction, "NO_TRADE"}:
            raise HorizonAuthorityError("HORIZON_TIMEFRAME_LINK_INVALID")
        if expected_role == "execution":
            execution_decision = persisted
    snapshot = (row.evidence or {}).get("execution_snapshot")
    if not isinstance(snapshot, dict):
        raise HorizonAuthorityError("HORIZON_EXECUTION_SNAPSHOT_MISSING")
    payload = execution_decision.decision_payload or {}
    payload_confidence = _first_not_none(payload.get("confidence"), payload.get("decision_confidence"))
    payload_confidence = (payload_confidence * 100 if isinstance(payload_confidence, (int, float))
                          and payload_confidence <= 1 else payload_confidence)
    snapshot_checks = {
        "authoritative_decision_id": execution_decision.decision_id,
        "user_id": row.user_id,
        "symbol": row.symbol,
        "execution_timeframe": row.authoritative_execution_timeframe,
        "direction": row.final_direction,
    }
    if any(snapshot.get(key) != value for key, value in snapshot_checks.items()):
        raise HorizonAuthorityError("HORIZON_EXECUTION_SNAPSHOT_MISMATCH")
    risk_approval = snapshot.get("risk_approval")
    if not isinstance(risk_approval, dict) or any((
        risk_approval.get("horizon_decision_id") != row.id,
        risk_approval.get("authoritative_execution_decision_id") != execution_decision.decision_id,
        risk_approval.get("user_id") != row.user_id,
        risk_approval.get("symbol") != row.symbol,
        risk_approval.get("trading_profile") != row.selected_profile,
        risk_approval.get("execution_timeframe") != row.authoritative_execution_timeframe,
    )):
        raise HorizonAuthorityError("HORIZON_EXECUTION_SNAPSHOT_MISMATCH")
    if not isinstance(risk_approval.get("approved_notional_ceiling_usd"), (int, float)) or risk_approval["approved_notional_ceiling_usd"] <= 0:
        raise HorizonAuthorityError("POSITION_SIZE_LIMIT_ZERO")
    if (
        snapshot.get("risk", {}).get("stop_price") != payload.get("recommended_stop")
        or snapshot.get("risk", {}).get("target_price") != payload.get("recommended_target")
        or snapshot.get("decision_price") != payload.get("reference_price")
        or snapshot.get("evidence", {}).get("expected_edge") != payload.get("expected_edge")
        or snapshot.get("evidence", {}).get("directional_confidence") != payload_confidence
        or snapshot.get("evidence", {}).get("market_data_revision") != str(payload.get("market_data_revision") or "unknown")
        or snapshot.get("evidence", {}).get("performance_snapshot_revision") != str(payload.get("performance_snapshot_revision") or "unknown")
    ):
        raise HorizonAuthorityError("HORIZON_EXECUTION_SNAPSHOT_MISMATCH")
    if not row.unanimous:
        raise HorizonAuthorityError("HORIZON_DECISION_NOT_READY")
    if consume_key is not None:
        try:
            db.add(TradingHorizonConsumption(horizon_decision_id=row.id,idempotency_key=consume_key,consumed_at=now))
            db.commit()
        except Exception:
            db.rollback()
            raise HorizonAuthorityError("HORIZON_DECISION_ALREADY_CONSUMED")
    row.execution_snapshot = snapshot
    row.execution_decision = execution_decision
    return row
