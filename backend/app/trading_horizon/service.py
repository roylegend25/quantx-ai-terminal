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

from app.core.config import settings

# Soft confirmation penalties (PAPER mode only - see _confirm_higher_timeframes).
# Bounded well below a value that could ever push a passing primary signal's
# reported confidence into rejection by itself: the penalty is informational
# (confirmed_confidence), never a second gate. Strong opposition is the only
# hard reject in soft mode, gated separately.
_NEUTRAL_SIBLING_PENALTY = 0.05
_WEAK_OPPOSE_SIBLING_PENALTY = 0.10
_NO_AGREEMENT_PENALTY = 0.15
_MAX_SOFT_PENALTY = 0.25


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


def _confirm_higher_timeframes(higher_timeframes: list[str], timeframes: dict[str, dict], primary_direction: str) -> dict:
    """Soft multi-timeframe confirmation (PAPER mode only).

    The primary/execution timeframe's own confidence and edge requirements
    are evaluated independently and unchanged elsewhere - this only scores
    the OTHER (higher) timeframes against it:
      - AGREED:   at least one higher timeframe shares the primary direction,
                  and none strongly oppose it. Small penalty per sibling that
                  isn't itself agreeing (neutral/stale/weak-oppose).
      - REJECTED: any higher timeframe opposes with its own confidence at or
                  above the same bar (active_drive_min_confidence) a primary
                  signal would need - a "strong" opposing conviction, not a
                  bare direction flip. This is the only hard reject here.
      - NEUTRAL:  no higher timeframe agrees and none strongly oppose (all
                  neutral/stale/weak-oppose). Reduces confidence but does not
                  reject - the primary signal still stands on its own
                  evidence per the requirement that neutral higher timeframes
                  never auto-reject.
    """
    directions, confidences, notes = [], [], []
    classifications = []
    for tf in higher_timeframes:
        value = timeframes.get(tf)
        stale = bool(value) and bool((value.get("data_status") or {}).get("stale"))
        unavailable = value is None or bool(value.get("error"))
        direction = _direction(value) if value else "NO_TRADE"
        confidence = (value.get("decision_confidence", value.get("confidence")) if value else None)
        directions.append(direction)
        confidences.append(confidence if isinstance(confidence, Number) else None)
        if unavailable or stale:
            classifications.append("STALE")
            notes.append(f"{tf} data is {'stale' if stale else 'unavailable'} - treated as non-confirming, not opposing")
        elif direction == primary_direction and direction in {"LONG", "SHORT"}:
            classifications.append("AGREE")
            notes.append(f"{tf} agrees with the primary direction ({direction})")
        elif direction in {"LONG", "SHORT"} and direction != primary_direction:
            strong = confidence is not None and confidence >= settings.active_drive_min_confidence
            classifications.append("OPPOSE_STRONG" if strong else "OPPOSE_WEAK")
            notes.append(f"{tf} shows {'strong' if strong else 'weak'} opposing conviction "
                         f"({direction}, confidence {confidence:.0%})" if confidence is not None
                         else f"{tf} shows opposing direction ({direction}) with no confidence reading")
        else:
            classifications.append("NEUTRAL")
            notes.append(f"{tf} is neutral (no directional signal)")

    opposing = [n for c, n in zip(classifications, notes) if c == "OPPOSE_STRONG"]
    agreeing = [n for c, n in zip(classifications, notes) if c == "AGREE"]
    if opposing:
        result, penalty = "REJECTED", 1.0
        reason = "Higher-timeframe confirmation rejected: " + "; ".join(opposing) + "."
    elif agreeing:
        result = "AGREED"
        sibling_penalty = sum(
            _WEAK_OPPOSE_SIBLING_PENALTY if c == "OPPOSE_WEAK" else _NEUTRAL_SIBLING_PENALTY if c in {"NEUTRAL", "STALE"} else 0.0
            for c in classifications
        )
        penalty = min(_MAX_SOFT_PENALTY, sibling_penalty)
        reason = "Higher-timeframe confirmation agreed: " + "; ".join(agreeing) + "."
    else:
        result, penalty = "NEUTRAL", _NO_AGREEMENT_PENALTY
        reason = ("No higher timeframe confirms or opposes the primary direction; "
                  "proceeding on primary evidence alone with a confidence penalty. "
                  + "; ".join(notes) + ".")
    return {
        "higher_timeframe_direction": directions, "higher_timeframe_confidence": confidences,
        "confirmation_result": result, "confirmation_penalty": round(penalty, 4),
        "confirmation_reason": reason, "confirmation_details": notes,
    }


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
    soft_confirmation: bool = False,
) -> dict:
    """soft_confirmation gates the multi-timeframe confirmation policy:
    False (default, used for every non-PAPER mode) keeps the original policy
    unchanged - every required timeframe (including the confirmation ones)
    must independently be execution-ready and unanimous. True (PAPER mode
    only, wired at the call site in app.api.timeframes) softens ONLY the
    confirmation timeframes: the primary/execution timeframe still
    independently needs its full confidence and edge - see
    _confirm_higher_timeframes for the confirmation semantics."""
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
    for timeframe in profile.required_timeframes:
        value = timeframes.get(timeframe)
        direction = _direction(value)
        available = value is not None and not value.get("error")
        eligible = available and bool(value.get("eligible_for_execution", direction != "NO_TRADE"))
        matrix.append({"timeframe": timeframe, "required": True, "direction": direction, "available": available, "eligible": eligible, "confidence": value.get("decision_confidence", value.get("confidence")) if value else None, "expected_edge": _edge(value)})
    for timeframe, value in timeframes.items():
        if timeframe in profile.required_timeframes:
            continue
        direction = _direction(value)
        matrix.append({"timeframe": timeframe, "required": False, "structural_bias": timeframe == profile.structural_bias_timeframe,
                       "direction": direction, "available": not value.get("error"),
                       "eligible": bool(value.get("eligible_for_execution", direction != "NO_TRADE")),
                       "confidence": value.get("decision_confidence", value.get("confidence")), "expected_edge": _edge(value)})

    blockers = []
    higher_timeframes = [tf for tf in profile.required_timeframes if tf != profile.execution_timeframe]
    primary = timeframes.get(profile.execution_timeframe)
    primary_direction = _direction(primary)
    primary_available = primary is not None and not primary.get("error")
    primary_eligible = primary_available and bool(primary.get("eligible_for_execution", primary_direction != "NO_TRADE"))
    primary_confidence = primary.get("decision_confidence", primary.get("confidence")) if primary else None

    if soft_confirmation:
        primary_timeframe_pass = bool(
            primary_available and primary_eligible and primary_direction in {"LONG", "SHORT"}
            and isinstance(primary_confidence, Number) and primary_confidence >= settings.active_drive_min_confidence
        )
        if not primary_available:
            blockers.append(f"Primary execution timeframe {profile.execution_timeframe} is unavailable")
        elif not primary_eligible or primary_direction == "NO_TRADE":
            blockers.append(f"Primary execution timeframe {profile.execution_timeframe} is not execution-ready")
        elif primary_confidence is None:
            blockers.append(f"Primary execution timeframe {profile.execution_timeframe} confidence is unavailable")
        elif primary_confidence < settings.active_drive_min_confidence:
            blockers.append(f"Primary execution timeframe {profile.execution_timeframe} confidence "
                             f"{primary_confidence:.0%} is below the required {settings.active_drive_min_confidence:.0%}")
        confirmation = _confirm_higher_timeframes(higher_timeframes, timeframes, primary_direction)
        if confirmation["confirmation_result"] == "REJECTED":
            blockers.append(confirmation["confirmation_reason"])
        direction = primary_direction if primary_timeframe_pass else "NO_TRADE"
        confirmed_confidence = (round(primary_confidence * (1 - confirmation["confirmation_penalty"]), 4)
                                 if isinstance(primary_confidence, Number) else None)
        unanimous = primary_timeframe_pass and confirmation["confirmation_result"] != "REJECTED"
    else:
        primary_timeframe_pass = None  # not evaluated under the strict policy - primary is just one of several unanimous votes
        required_directions = []
        for timeframe in profile.required_timeframes:
            value = timeframes.get(timeframe)
            tf_direction = _direction(value)
            available = value is not None and not value.get("error")
            eligible = available and bool(value.get("eligible_for_execution", tf_direction != "NO_TRADE"))
            required_directions.append(tf_direction if available and eligible else "NO_TRADE")
            if not available:
                blockers.append(f"Required timeframe {timeframe} is unavailable")
            elif not eligible or tf_direction == "NO_TRADE":
                blockers.append(f"Required timeframe {timeframe} is not execution-ready")
            elif value.get("decision_confidence", value.get("confidence")) is None:
                blockers.append(f"Required timeframe {timeframe} confidence is unavailable")
        unanimous = bool(required_directions) and len(set(required_directions)) == 1 and required_directions[0] in {"LONG", "SHORT"}
        if not unanimous and not any("not execution-ready" in b or "unavailable" in b for b in blockers):
            blockers.append("Required timeframes are not unanimous")
        direction = required_directions[0] if unanimous else "NO_TRADE"
        confirmation = {"higher_timeframe_direction": [], "higher_timeframe_confidence": [],
                        "confirmation_result": "STRICT_MODE_NOT_APPLICABLE", "confirmation_penalty": 0.0,
                        "confirmation_reason": "Strict unanimity policy is active (non-PAPER mode); "
                                               "soft confirmation is not evaluated.", "confirmation_details": []}
        confirmed_confidence = primary_confidence if isinstance(primary_confidence, Number) else None

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
        (f"Why This Trade: primary timeframe {profile.execution_timeframe} supports {direction} with confirmation "
         f"result {confirmation['confirmation_result']}; expected edge meets the profile threshold."
         if soft_confirmation else
         f"Why This Trade: all required timeframes ({', '.join(profile.required_timeframes)}) unanimously support {direction}; "
         f"{profile.execution_timeframe} is the sole execution authority and expected edge meets the profile threshold.")
        if ready else "Why No Trade: " + "; ".join(blockers) + "."
    )
    return {
        "primary_timeframe_pass": primary_timeframe_pass,
        "higher_timeframe_direction": confirmation["higher_timeframe_direction"],
        "higher_timeframe_confidence": confirmation["higher_timeframe_confidence"],
        "confirmation_result": confirmation["confirmation_result"],
        "confirmation_penalty": confirmation["confirmation_penalty"],
        "confirmation_reason": confirmation["confirmation_reason"],
        "confirmed_confidence": confirmed_confidence,
        "soft_confirmation_policy_active": soft_confirmation,
        "profile_decision_id": None, "user_id": user_id,
        "engine": engine_id, "engine_version": engine_version,
        "generated_at": now.isoformat(), "expires_at": expires_at.isoformat(),
        "symbol": symbol.upper(), "requested_profile": profile_key, "selected_profile": selected_mode,
        "resolved_profile": profile.key, "profile_label": profile.label, "selection_reason": selection_reason,
        "execution_timeframe": profile.execution_timeframe, "chart_timeframe": None,
        "required_timeframes": list(profile.required_timeframes),
        "strict_unanimity_required": not soft_confirmation, "unanimity_passed": unanimous,
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
