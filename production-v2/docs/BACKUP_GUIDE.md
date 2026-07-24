# Backup Guide

## What gets backed up, and why

| Item | Method | Why |
|---|---|---|
| `paper.db` (SQLite, WAL mode) | `Connection.backup()` via Python's stdlib `sqlite3` (see `scripts/backup.sh`) | Safe to run against a live database under WAL — this is the same API-level online backup approach as the `sqlite3 .backup` CLI command, just invoked from Python since the image doesn't ship the CLI. A raw `cp` of a WAL-mode DB file while it's being written to can copy a torn/inconsistent file. |
| `.env` | Plain copy, `chmod 600` | Secrets — restrict access on the backup destination the same as on the VM. |
| Redis | **Not backed up** | Ephemeral by design (`ARCHITECTURE.md`) — cache + rate-limit state only, safe to lose, rebuilds itself within seconds of restart. |
| Docker images | **Not backed up separately** | Rebuildable from any git commit via `docker build`; `docs/ROLLBACK_GUIDE.md` covers keeping the previous one on disk. |

## Schedule

`quantx-v2-backup.timer` runs `scripts/backup.sh` daily at 03:15 UTC (low-traffic window,
configurable in `systemd/quantx-v2-backup.timer`). Retention is `BACKUP_RETENTION_DAYS` in `.env`
(default 14 days), pruned automatically at the end of every backup run.

```bash
systemctl status quantx-v2-backup.timer      # confirm it's scheduled
systemctl list-timers quantx-v2-backup.timer # see next run time
sudo journalctl -u quantx-v2-backup.service --since today   # last run's output
```

## Manual backup (before any risky operation)

```bash
cd /opt/quantx/production-v2
./scripts/backup.sh
```
`scripts/update.sh` already calls this automatically before every deploy — you don't need to run
it separately before a normal update.

## Off-box copies (do this — a backup that lives only on the VM it's protecting against isn't one)

Nothing in this kit ships an off-box copy step by default (deliberately — see "STOP. Do not
migrate anything" in this project's directive: adding a cross-VM/cross-cloud sync step wasn't
asked for and shouldn't be invented silently). Recommended, low-risk addition once you're ready:
a `gsutil rsync` (or `rclone`) of `$BACKUP_DIR` to a GCS bucket at the end of `scripts/backup.sh`,
gated behind its own env var so it's an explicit opt-in:
```bash
# append to scripts/backup.sh, after the prune step, only if BACKUP_GCS_BUCKET is set:
[ -n "${BACKUP_GCS_BUCKET:-}" ] && gsutil -m rsync -r "$DEST_DIR" "gs://${BACKUP_GCS_BUCKET}/${TIMESTAMP}/"
```

## Restoring

See `docs/DISASTER_RECOVERY.md` — restoring is a full procedure with its own safety checks
(maintenance mode, verifying the restore point, confirming trading mode), not a one-liner.

## Verifying a backup is actually restorable (do this periodically, not just on faith)

```bash
sqlite3 /opt/quantx/backups/<timestamp>/paper.db "PRAGMA integrity_check;"   # expect: ok
sqlite3 /opt/quantx/backups/<timestamp>/paper.db "SELECT COUNT(*) FROM prediction_ledger;"
```
(Run these from any machine with the `sqlite3` CLI — e.g. your workstation, or a throwaway
container: `docker run --rm -v /opt/quantx/backups:/b nouchka/sqlite3 sqlite3 /b/<ts>/paper.db "PRAGMA integrity_check;"`.)
