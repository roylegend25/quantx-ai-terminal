# Database schema administration

Normal API and worker startup runs a read-only physical compatibility check. It does not create tables or apply additive migrations. If required tables, legacy additive columns, recorded Horizon revisions, indexes, or PostgreSQL immutability triggers are absent, automated scheduling remains disabled with `TRADING_HORIZON_MIGRATION_REQUIRED`.

For a controlled deployment, first back up the target database and confirm the sanitized dialect, host, and database name:

```bash
cd backend
python -m app.db.schema_admin status
python -m app.db.schema_admin dry-run
python -m app.db.schema_admin upgrade
```

The explicit upgrade creates missing base tables, applies the legacy unrevisioned additive stages (trade provenance/risk, prediction features, Model Lab, protection/provider, risk settings, and Active Drive V2 ledger fields), then applies recorded revisions `20260715_01_trading_horizon_authority` and `20260716_02_horizon_issuance_fingerprint`. It finishes with physical verification; a failed or partial stage is not reported as compatible.

Fresh databases and existing databases with none, some, or all additive columns are supported. Existing values are preserved and new historical fields remain nullable unless their original migration defines a safe default. The pipeline is rerun-safe. Ambiguous or destructive schema drift is not repaired automatically; restore from backup or perform a separately reviewed migration before retrying.
