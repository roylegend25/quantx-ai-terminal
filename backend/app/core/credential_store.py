"""Encrypted-at-rest storage for the admin-managed Binance Real API
credential (Phase 32).

Storage tier chosen deliberately: this deployment has no existing cloud
secret-manager integration wired into the app (no Vault, no Secret Manager
client anywhere in the codebase) and verifying IAM entitlement for Google
Secret Manager from here isn't something this module can safely assume -
so per the required "never silently fall back to plaintext" rule, this
uses the next tier down that IS fully verifiable and already available:
Fernet symmetric encryption (from `cryptography`, already a dependency)
with a server-only master key (settings.credential_encryption_key, set
once via .env, never returned by any endpoint). This mirrors how
SECRET_KEY/ADMIN_PASSWORD_HASH are already handled in this codebase - an
operational secret that lives in the server environment only.

If the master key is missing, every function here raises
CredentialStoreNotConfigured rather than storing plaintext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CredentialStoreNotConfigured(RuntimeError):
    """CREDENTIAL_ENCRYPTION_KEY is not set - refuse to store or read
    anything rather than ever falling back to plaintext."""


class CredentialDecryptionError(RuntimeError):
    """Stored ciphertext could not be decrypted with the current master
    key (key rotated without re-encrypting, or corrupted data)."""


def _fernet() -> Fernet:
    key = settings.credential_encryption_key
    if not key:
        raise CredentialStoreNotConfigured(
            "CREDENTIAL_ENCRYPTION_KEY is not configured on the server - "
            "credential storage is disabled rather than falling back to plaintext"
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise CredentialStoreNotConfigured(f"CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key: {e!r}")


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise CredentialDecryptionError("Stored credential could not be decrypted") from e


def fingerprint(api_key: str) -> str:
    """A masked display string safe to return from an API response - never
    enough of the real key to reconstruct it, just enough for the operator
    to recognize which key is stored."""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


def store_configured() -> bool:
    return bool(settings.credential_encryption_key)
