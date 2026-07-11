"""Safe, allowlist-only editor for the deployment's .env file (Phase 24).

Hard rules, enforced structurally:
  - Only the keys in ALLOWED_ENV_KEYS can ever be written. Everything else
    - secrets, unknown keys, casing variants - is rejected before any file
    is touched. There is no code path here that can write BINANCE_API_KEY,
    BINANCE_API_SECRET or any other non-allowlisted variable.
  - Nothing here ever RETURNS or LOGS the value of a non-allowlisted key.
    read_env_values() only extracts allowed keys; the rest of the file is
    treated as opaque lines that are preserved verbatim (comments, blank
    lines, secrets) and never inspected beyond splitting off the key name.
  - Every write goes: validate -> timestamped backup -> temp file + atomic
    os.replace -> chmod 600. When the .env is a Docker bind-mounted FILE,
    os.replace onto the mount point fails with EBUSY - we then fall back to
    an in-place rewrite (the backup already exists at that point), because
    swapping the inode would silently detach the container copy from the
    host file.

Runtime application: docker-compose injects env vars at container CREATE
time, and pydantic-settings prefers real env vars over the dotenv file, so
a stale injected value would win on a plain process restart. Therefore the
.env FILE is made authoritative for the allowed keys: apply_to_settings()
re-reads them onto the live `settings` object at startup and after every
edit - changes take effect immediately, no restart required.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone

from app.core.config import settings

# The ONLY environment variables this module will ever write.
ALLOWED_ENV_KEYS = {
    "BINANCE_LIVE_ENABLED",
    "BINANCE_DEFAULT_LEVERAGE",
    "BINANCE_MAX_LEVERAGE",
    "BINANCE_MAX_NOTIONAL_PER_TRADE",
    "BINANCE_MAX_DAILY_LOSS_USDT",
    "BINANCE_ALLOWED_SYMBOLS",
}

# Documentation + explicit test surface: these must NEVER be editable. The
# allowlist above already excludes them (and everything else); this set
# exists so tests can assert the intent by name.
FORBIDDEN_ENV_KEYS = {
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "SECRET_KEY",
    "DATABASE_URL",
    "PAPER_DATABASE_URL",
    "JWT_SECRET",
    "SMTP_PASSWORD",
    "ADMIN_PASSWORD_HASH",
    "TELEGRAM_BOT_TOKEN",
}

# Map .env keys -> Settings attributes, with a parser each so runtime
# application uses exactly the coercion the field expects.
_TRUE_SET = {"1", "true", "yes", "on"}


def _parse_bool(v: str) -> bool:
    return v.strip().lower() in _TRUE_SET


def _parse_symbols(v: str) -> list[str]:
    return [s.strip().upper() for s in v.split(",") if s.strip()]


_SETTINGS_FIELDS: dict[str, tuple[str, callable]] = {
    "BINANCE_LIVE_ENABLED": ("binance_live_enabled", _parse_bool),
    "BINANCE_DEFAULT_LEVERAGE": ("binance_default_leverage", float),
    "BINANCE_MAX_LEVERAGE": ("binance_max_leverage", float),
    "BINANCE_MAX_NOTIONAL_PER_TRADE": ("binance_max_notional_per_trade", float),
    "BINANCE_MAX_DAILY_LOSS_USDT": ("binance_max_daily_loss_usdt", float),
    "BINANCE_ALLOWED_SYMBOLS": ("binance_allowed_symbols", _parse_symbols),
}

_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


class EnvUpdateError(ValueError):
    """A rejected env update - safe to surface verbatim to the UI."""


def env_file_path() -> str:
    """Where the deployment's .env lives. ENV_FILE_PATH wins (tests, custom
    deploys); inside the container the compose file bind-mounts the host's
    root .env at /app/.env; a bare local checkout falls back to the repo
    root next to docker-compose.yml."""
    explicit = os.environ.get("ENV_FILE_PATH")
    if explicit:
        return explicit
    if os.path.exists("/app/.env"):
        return "/app/.env"
    here = os.path.dirname(os.path.abspath(__file__))  # backend/app/core
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".env"))


def _line_key(line: str) -> str | None:
    m = _KEY_RE.match(line)
    return m.group(1) if m else None


def read_env_values(path: str | None = None) -> dict[str, str]:
    """Current file values of ALLOWED keys only - everything else in the
    file is never parsed past its key name, let alone returned."""
    path = path or env_file_path()
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            key = _line_key(line)
            if key in ALLOWED_ENV_KEYS:
                values[key] = line.split("=", 1)[1].strip()
    return values


def validate_updates(updates: dict) -> dict[str, str]:
    """Allowlist + value validation. Returns the normalized {KEY: value}
    strings to write. Raises EnvUpdateError naming only the offending KEY
    (never any value that isn't ours to show)."""
    if not updates:
        raise EnvUpdateError("No settings provided")

    normalized: dict[str, str] = {}
    for raw_key, raw_value in updates.items():
        key = str(raw_key).strip()
        if key not in ALLOWED_ENV_KEYS:
            raise EnvUpdateError(f"{key} is not an editable setting")

        if key == "BINANCE_LIVE_ENABLED":
            if isinstance(raw_value, bool):
                normalized[key] = "true" if raw_value else "false"
            elif str(raw_value).strip().lower() in ("true", "false"):
                normalized[key] = str(raw_value).strip().lower()
            else:
                raise EnvUpdateError("BINANCE_LIVE_ENABLED must be true or false")
            continue

        if key == "BINANCE_ALLOWED_SYMBOLS":
            if isinstance(raw_value, (list, tuple)):
                symbols = [str(s).strip().upper() for s in raw_value if str(s).strip()]
            else:
                symbols = _parse_symbols(str(raw_value))
            if not symbols:
                raise EnvUpdateError("BINANCE_ALLOWED_SYMBOLS cannot be empty")
            for s in symbols:
                if not _SYMBOL_RE.match(s):
                    raise EnvUpdateError(f"{s} is not a valid USDT futures symbol")
            normalized[key] = ",".join(symbols)
            continue

        # numeric limits
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise EnvUpdateError(f"{key} must be a number")
        if key in ("BINANCE_DEFAULT_LEVERAGE", "BINANCE_MAX_LEVERAGE") and value < 1:
            raise EnvUpdateError(f"{key} must be at least 1")
        if key in ("BINANCE_MAX_NOTIONAL_PER_TRADE", "BINANCE_MAX_DAILY_LOSS_USDT") and value <= 0:
            raise EnvUpdateError(f"{key} must be positive")
        normalized[key] = f"{value:g}"

    return normalized


def update_env_file(updates: dict, path: str | None = None) -> dict[str, dict[str, str | None]]:
    """Validate, back up, and write the allowed updates. Comments and every
    non-updated line are preserved byte-for-byte. Returns
    {KEY: {"old": ..., "new": ...}} for the audit trail - only allowed
    (non-secret) keys can ever appear here."""
    normalized = validate_updates(updates)
    path = path or env_file_path()

    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    old_values = read_env_values(path)

    remaining = dict(normalized)
    out: list[str] = []
    for line in lines:
        key = _line_key(line)
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, value in remaining.items():  # keys not present yet get appended
        out.append(f"{key}={value}")
    content = "\n".join(out) + "\n"

    directory = os.path.dirname(os.path.abspath(path)) or "."

    # backup BEFORE any write so a failed write can never lose state.
    # ENV_BACKUP_DIR points backups at a PERSISTENT location - inside the
    # container only /app/.env itself is bind-mounted, so a sibling backup
    # would die with the container; compose sets this to the mounted
    # /app/data volume instead.
    if os.path.exists(path):
        backup_dir = os.environ.get("ENV_BACKUP_DIR") or directory
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(backup_dir, f".env.backup.{stamp}")
        with open(path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())
        os.chmod(backup_path, 0o600)

    fd, tmp_path = tempfile.mkstemp(prefix=".env.tmp.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        try:
            os.replace(tmp_path, path)  # atomic on the same filesystem
        except OSError:
            # path is a Docker bind-mounted FILE: replacing the inode is
            # refused (EBUSY) / would detach it from the host. Rewrite in
            # place instead - the backup above already secured the old
            # contents.
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.unlink(tmp_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise

    os.chmod(path, 0o600)

    return {
        key: {"old": old_values.get(key), "new": value}
        for key, value in normalized.items()
    }


def apply_to_settings(path: str | None = None, target=None) -> dict[str, object]:
    """Push the .env file's ALLOWED keys onto the live settings object,
    making the file authoritative for them (stale env vars injected at
    container-create time would otherwise win on restart). Returns the
    applied {attribute: value} map - all safe, non-secret values."""
    target = target or settings
    applied: dict[str, object] = {}
    for key, raw in read_env_values(path).items():
        attr, parse = _SETTINGS_FIELDS[key]
        try:
            value = parse(raw)
        except (TypeError, ValueError):
            continue  # a hand-mangled value must not crash startup
        setattr(target, attr, value)
        applied[attr] = value
    return applied
