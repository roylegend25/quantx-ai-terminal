# Active Drive V2 rollout and rollback

## Current state

The implementation is deployed and test-verified. The account was confirmed flat before migration. Active Drive V2 is authoritative and currently fails closed to NO_TRADE while evidence is insufficient.

## Future redeployment prerequisites

1. Confirm the live position is closed or independently protected on Binance.
2. Lock live execution during deployment from the existing authenticated control.
3. Repeat the read-only protection check and require zero unprotected positions.
4. Confirm `backups/active-drive-v2-20260715T021159Z/paper.db` remains non-empty and passes `PRAGMA integrity_check`.
5. Take a fresh SQLite backup immediately before deployment.

## Apply and verify

The migration is additive: startup creates the new tables and only adds nullable provenance columns. It does not delete or backfill legacy prediction outcomes.

```bash
cd ~/quantx-ai-terminal
python3 -m py_compile $(find backend/app backend/tests -name '*.py')
cd frontend && npm run test && npm run build && cd ..
docker compose up -d --build
curl -fsS https://www.quantxterminal.com/api/health
curl -I https://www.quantxterminal.com
```

After authenticated login, verify `GET /api/bot/decision-engine` reports `active_drive_v2`. Do not expose the bearer token in shell history or logs.

## Ordinary engine rollback

Use Bot Settings → Decision Engine → Active Drive V1 → acknowledge → confirm. This changes only future decisions. It does not change positions, orders, balances, TP/SL, or historical records. A code/database rollback is not required for an ordinary engine switch.

## Code rollback

Only use a code rollback if the application itself fails after deployment. Preserve the new append-only tables. Restore the SQLite backup only if the additive schema migration fails and only after stopping the backend; never restore a database merely to switch engines.
