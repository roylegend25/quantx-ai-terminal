"""Explicit schema status and upgrade command. Never imported by startup."""
import argparse

from app.db.init_db import check_schema_compatibility, initialize_schema
from app.db.session import engine


def target_identity(bind=engine) -> dict:
    url = bind.url
    return {"dialect": bind.dialect.name, "host": url.host or "local",
            "database": url.database or "unknown"}


def status(bind=engine) -> dict:
    result = {**target_identity(bind), **check_schema_compatibility(bind)}
    result["current_revision"] = (result["issuance_revision"] if result["issuance_applied"]
                                  else result["base_revision"] if result["base_applied"] else None)
    result["pending_revisions"] = [revision for revision, applied in (
        (result["base_revision"], result["base_applied"]),
        (result["issuance_revision"], result["issuance_applied"]),
    ) if not applied]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "dry-run", "upgrade"))
    args = parser.parse_args()
    before = status(engine)
    print({key: before[key] for key in ("dialect", "host", "database", "current_revision",
          "base_applied", "issuance_applied", "pending_revisions", "compatible")})
    print({"legacy_additive_stages": before["legacy_additive_stages"],
           "missing_tables": before["missing_tables"],
           "missing_columns": before["missing_columns"],
           "physical_schema_verified": before["physical_schema_verified"]})
    if args.action == "upgrade":
        initialize_schema(engine)
        print({"overall_schema_status": "compatible" if status(engine)["compatible"] else "migration required"})
    elif args.action == "dry-run":
        print({"pending_revisions": before["pending_revisions"]})


if __name__ == "__main__":
    main()
