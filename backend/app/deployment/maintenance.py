from __future__ import annotations

import os
from datetime import datetime, timezone

from app.core.config import settings


def marker_path() -> str:
    return settings.deployment_maintenance_file


def enabled() -> bool:
    return bool(settings.deployment_maintenance_mode or os.path.exists(marker_path()))


def enable(reason: str = "deployment") -> None:
    path = marker_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(f"{datetime.now(timezone.utc).isoformat()} {reason}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    settings.deployment_maintenance_mode = True


def disable() -> None:
    try:
        os.unlink(marker_path())
    except FileNotFoundError:
        pass
    settings.deployment_maintenance_mode = False


def status() -> dict:
    return {
        "enabled": enabled(),
        "marker_present": os.path.exists(marker_path()),
        "reason": "DEPLOYMENT_MAINTENANCE" if enabled() else None,
    }
