"""Stage 2 performance fix: drop the exact-duplicate index on prediction_ledger.

ix_prediction_ledger_deadline and ix_prediction_ledger_resolution_deadline
were both `CREATE INDEX ... ON prediction_ledger (resolution_deadline)` -
byte-for-byte the same index under two names. prediction_ledger is a
307k+ row table written to on every prediction and every resolver cycle, so
every extra index is paid for on every insert/update. This was the only
provably-exact duplicate found across the whole database (checked
programmatically by grouping every index by (table, column list), not by
eyeballing names) - the rest of prediction_ledger's ~19 remaining indexes
have distinct column lists serving distinct query shapes and were
deliberately left alone rather than guessed at.

Idempotent: safe to run again if the duplicate is already gone.

Usage (from inside the backend container, or any environment with access
to the same paper.db):
    python -m scripts.drop_duplicate_prediction_ledger_index [--db-path PATH]

Back up the database before running this against a live file that isn't
already backed up.
"""
import argparse
import sqlite3

DUPLICATE_INDEX = "ix_prediction_ledger_deadline"
SURVIVING_INDEX = "ix_prediction_ledger_resolution_deadline"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="/app/data/paper.db")
    args = parser.parse_args()

    con = sqlite3.connect(args.db_path)
    try:
        cur = con.cursor()
        existing = {
            name
            for (name,) in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='prediction_ledger'"
            ).fetchall()
        }
        if DUPLICATE_INDEX not in existing:
            print(f"{DUPLICATE_INDEX} already absent - nothing to do.")
            return
        if SURVIVING_INDEX not in existing:
            raise SystemExit(
                f"Refusing to drop {DUPLICATE_INDEX}: {SURVIVING_INDEX} (the index meant to "
                "survive and keep resolution_deadline covered) is not present. Investigate "
                "before dropping - this check exists so this script can never remove the "
                "only remaining index on that column."
            )
        cur.execute(f"DROP INDEX {DUPLICATE_INDEX}")
        con.commit()
        print(f"Dropped {DUPLICATE_INDEX} ({SURVIVING_INDEX} still covers resolution_deadline).")
    finally:
        con.close()


if __name__ == "__main__":
    main()
