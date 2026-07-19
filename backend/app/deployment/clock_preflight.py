"""Pure clock-safety checks shared by deployment tooling and tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClockPreflight:
    ok: bool
    status: str
    reason: str | None
    host_container_delta_ms: float
    ntp_synchronized: bool
    binance_time_status: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "reason": self.reason,
            "host_container_delta_ms": round(self.host_container_delta_ms, 3),
            "ntp_synchronized": self.ntp_synchronized,
            "binance_time_status": self.binance_time_status,
        }


def evaluate_clock_preflight(
    *,
    ntp_synchronized: bool,
    host_time_ms: float,
    container_time_ms: float,
    binance_time_status: str,
    maximum_host_container_delta_ms: float = 1_000.0,
) -> ClockPreflight:
    delta = container_time_ms - host_time_ms
    reason = None
    if not ntp_synchronized:
        reason = "host NTP is not synchronized"
    elif abs(delta) > maximum_host_container_delta_ms:
        reason = "host and container clocks differ beyond the safety bound"
    elif binance_time_status != "synced":
        reason = f"Binance timestamp status is {binance_time_status}"
    return ClockPreflight(
        ok=reason is None,
        status="synced" if reason is None else "unsafe",
        reason=reason,
        host_container_delta_ms=delta,
        ntp_synchronized=ntp_synchronized,
        binance_time_status=binance_time_status,
    )
