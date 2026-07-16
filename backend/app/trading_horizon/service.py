"""Trading Horizon policy.

This module does not place orders.  It converts Active Drive V2 timeframe
decisions into one execution intent, or a fully explained NO_TRADE result.
Keeping this policy pure makes it usable by paper/live callers without
creating a second decision engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from numbers import Number


@dataclass(frozen=True)
class TradingProfile:
    key: str
    label: str
    execution_timeframe: str
    required_timeframes: tuple[str, ...]
    structural_bias_timeframe: str
    minimum_edge: float
    holding_window: str
    max_holding_seconds: int
    stop_atr: float


PROFILES = {
    "short_term": TradingProfile("short_term", "Short-Term Trading", "5m", ("5m", "15m", "1h"), "4h", .0025, "1–6 hours", 6 * 3600, 1.25),
    "mid_term": TradingProfile("mid_term", "Mid-Term Trading", "15m", ("15m", "1h", "4h"), "1d", .006, "1–5 days", 5 * 86400, 1.75),
    "safe": TradingProfile("safe", "Safe Trading", "1h", ("1h", "4h", "1d"), "1w", .012, "3–14 days", 14 * 86400, 2.25),
}


def _direction(value: dict | None) -> str:
    direction = str((value or {}).get("final_signal") or (value or {}).get("signal") or (value or {}).get("direction") or "NO_TRADE").upper()
    return direction if direction in {"LONG", "SHORT"} else "NO_TRADE"


def _edge(value: dict | None) -> float | None:
    raw = (value or {}).get("expected_edge")
    return float(raw) if isinstance(raw, Number) and not isinstance(raw, bool) else None


def previous_oos_comparison(resolutions: Iterable[dict], current_direction: str) -> dict:
    resolved = [r for r in resolutions if str(r.get("outcome", "")).upper() not in {"", "PENDING", "UNRESOLVED"}]
    previous = resolved[-1] if resolved else None
    if not previous:
        return {"available": False, "message": "No previously resolved out-of-sample prediction is available."}
    previous_direction = str(previous.get("direction") or previous.get("signal") or "NO_TRADE").upper()
    actual_return = previous.get("actual_return")
    resolved_return = actual_return if actual_return is not None else previous.get("return_pct")
    return {
        "available": True,
        "prediction_id": previous.get("id") or previous.get("prediction_id"),
        "direction": previous_direction,
        "outcome": previous.get("outcome"),
        "actual_return": resolved_return,
        "same_direction": previous_direction == current_direction,
        "message": f"Previous resolved OOS call was {previous_direction} and resolved {str(previous.get('outcome')).upper()}.",
    }


def compare_profile_edges(timeframes: dict[str, dict]) -> list[dict]:
    rows = []
    for profile in PROFILES.values():
        edge = _edge(timeframes.get(profile.execution_timeframe))
        rows.append({
            "profile": profile.key,
            "label": profile.label,
            "timeframe": profile.execution_timeframe,
            "expected_edge": edge,
            "required_edge": profile.minimum_edge,
            "passed": edge is not None and edge >= profile.minimum_edge
                      and bool((timeframes.get(profile.execution_timeframe) or {}).get("current_edge_supported",
                               (timeframes.get(profile.execution_timeframe) or {}).get("edge_supported", False))),
        })
    return rows


def select_auto_profile(timeframes: dict[str, dict]) -> tuple[TradingProfile, str]:
    comparisons = compare_profile_edges(timeframes)
    eligible = [row for row in comparisons if row["passed"]]
    if eligible:
        best = max(eligible, key=lambda row: abs(row["expected_edge"]))
        return PROFILES[best["profile"]], f"Auto Adaptive selected the strongest threshold-qualified expected edge ({best['timeframe']})."
    # Short term is the least data-hungry fallback, but remains blocked by
    # the edge/readiness checks below; auto selection never fabricates readiness.
    return PROFILES["short_term"], "Auto Adaptive found no threshold-qualified edge; Short-Term is selected for evaluation only."


def build_horizon_decision(
    symbol: str,
    timeframes: dict[str, dict],
    profile_key: str = "auto",
    *,
    price: float | None = None,
    atr: float | None = None,
    resolutions: Iterable[dict] = (),
    now: datetime | None = None,
    user_id: str = "internal-scheduler",
    engine_id: str = "active_drive_v2",
    engine_version: str = "2.0.0",
    historical_edge_summary: dict | None = None,
) -> dict:
    requested_profile = profile_key.lower().replace("-", "_")
    if requested_profile in {"auto", "auto_adaptive"}:
        profile, selection_reason = select_auto_profile(timeframes)
        selected_mode = "auto_adaptive"
    else:
        if requested_profile not in PROFILES:
            raise ValueError(f"Unknown Trading Horizon profile: {profile_key}")
        profile = PROFILES[requested_profile]
        selection_reason = f"{profile.label} was selected explicitly."
        selected_mode = profile.key

    matrix = []
    required_directions = []
    blockers = []
    for timeframe in profile.required_timeframes:
        value = timeframes.get(timeframe)
        direction = _direction(value)
        available = value is not None and not value.get("error")
        eligible = available and bool(value.get("eligible_for_execution", direction != "NO_TRADE"))
        matrix.append({"timeframe": timeframe, "required": True, "direction": direction, "available": available, "eligible": eligible, "confidence": value.get("decision_confidence", value.get("confidence")) if value else None, "expected_edge": _edge(value)})
        required_directions.append(direction if available and eligible else "NO_TRADE")
        if not available:
            blockers.append(f"Required timeframe {timeframe} is unavailable")
        elif not eligible or direction == "NO_TRADE":
            blockers.append(f"Required timeframe {timeframe} is not execution-ready")
        elif value.get("decision_confidence", value.get("confidence")) is None:
            blockers.append(f"Required timeframe {timeframe} confidence is unavailable")

    for timeframe, value in timeframes.items():
        if timeframe in profile.required_timeframes:
            continue
        direction = _direction(value)
        matrix.append({"timeframe": timeframe, "required": False, "structural_bias": timeframe == profile.structural_bias_timeframe,
                       "direction": direction, "available": not value.get("error"),
                       "eligible": bool(value.get("eligible_for_execution", direction != "NO_TRADE")),
                       "confidence": value.get("decision_confidence", value.get("confidence")), "expected_edge": _edge(value)})

    unanimous = bool(required_directions) and len(set(required_directions)) == 1 and required_directions[0] in {"LONG", "SHORT"}
    if not unanimous and not any("not execution-ready" in b or "unavailable" in b for b in blockers):
        blockers.append("Required timeframes are not unanimous")
    direction = required_directions[0] if unanimous else "NO_TRADE"
    authority = timeframes.get(profile.execution_timeframe) or {}
    expected_edge = _edge(authority)
    current_edge_supported = bool(authority.get("current_edge_supported", authority.get("edge_supported", False)))
    if expected_edge is None or not current_edge_supported:
        blockers.append("EXPECTED_EDGE_NOT_SUPPORTED")
    elif expected_edge < profile.minimum_edge:
        blockers.append(f"Expected edge {expected_edge:.2%} is below the {profile.minimum_edge:.2%} profile minimum")

    now = now or datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=120)
    invalidation_price = None
    if price is not None and atr is not None and direction in {"LONG", "SHORT"}:
        invalidation_price = price - profile.stop_atr * atr if direction == "LONG" else price + profile.stop_atr * atr
    bias = timeframes.get(profile.structural_bias_timeframe)
    bias_direction = _direction(bias)
    if bias is None or bias.get("error"):
        blockers.append(f"Structural-bias timeframe {profile.structural_bias_timeframe} is unavailable")
    elif bias_direction not in {direction, "NO_TRADE"}:
        blockers.append(f"Structural-bias timeframe {profile.structural_bias_timeframe} conflicts with required timeframes")
    ready = not blockers
    final_direction = direction if ready else "NO_TRADE"
    explanation = (
        f"Why This Trade: all required timeframes ({', '.join(profile.required_timeframes)}) unanimously support {direction}; "
        f"{profile.execution_timeframe} is the sole execution authority and expected edge meets the profile threshold."
        if ready else "Why No Trade: " + "; ".join(blockers) + "."
    )
    return {
        "profile_decision_id": None, "user_id": user_id,
        "engine": engine_id, "engine_version": engine_version,
        "generated_at": now.isoformat(), "expires_at": expires_at.isoformat(),
        "symbol": symbol.upper(), "requested_profile": profile_key, "selected_profile": selected_mode,
        "resolved_profile": profile.key, "profile_label": profile.label, "selection_reason": selection_reason,
        "execution_timeframe": profile.execution_timeframe, "chart_timeframe": None,
        "required_timeframes": list(profile.required_timeframes),
        "strict_unanimity_required": True, "unanimity_passed": unanimous,
        "confirmation_timeframes": [tf for tf in profile.required_timeframes if tf != profile.execution_timeframe],
        "structural_bias_timeframe": profile.structural_bias_timeframe,
        "direction": final_direction, "ready": ready, "blockers": blockers, "explanation": explanation,
        "estimated_holding_window": profile.holding_window,
        "price_invalidation": {"price": round(invalidation_price, 8) if invalidation_price is not None else None, "basis": f"{profile.stop_atr:g} ATR from entry", "available": invalidation_price is not None},
        "time_invalidation": {"at": (now + timedelta(seconds=profile.max_holding_seconds)).isoformat(), "max_seconds": profile.max_holding_seconds},
        "agreement_matrix": matrix, "expected_edge": expected_edge,
        "current_edge": expected_edge, "current_expected_edge": expected_edge,
        "current_edge_supported": current_edge_supported,
        "historical_edge_summary": historical_edge_summary or {},
        "edge_comparison": compare_profile_edges(timeframes),
        "previous_resolved_oos": previous_oos_comparison(resolutions, final_direction),
        "durable_idempotency_available": True, "profile_persistence_available": True,
    }
