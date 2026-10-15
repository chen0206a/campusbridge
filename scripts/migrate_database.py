"""Business migrations and explicit SQLite cutover. Credentials are environment-only."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from campusbridge.data_migration import copy_business, split_knowledge
from campusbridge.db import Database
from campusbridge.schema import migrate, verify_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("upgrade", "verify", "adopt-legacy-sqlite", "split-knowledge", "copy-business"))
    parser.add_argument("--sqlite", type=Path, help="Business SQLite target, or readonly source for copy/split")
    parser.add_argument("--knowledge-target", type=Path)
    args = parser.parse_args()
    db = None
    try:
        if args.command == "split-knowledge":
            if not args.sqlite or not args.knowledge_target: parser.error("Source and new knowledge target required")
            result = split_knowledge(args.sqlite, args.knowledge_target)
        else:
            url = os.environ.get("CAMPUSBRIDGE_MIGRATION_DATABASE_URL")
            if not url and args.sqlite is None: parser.error("Set migration URL in environment or pass --sqlite")
            if args.command == "adopt-legacy-sqlite" and url: parser.error("Adoption is SQLite-only")
            db = Database(None if url else args.sqlite, url=url, scope="business", schema="none")
            if args.command == "copy-business":
                result = copy_business(args.sqlite, db)
            else:
                db.acquire_runtime_owner()
                if args.command != "verify": migrate(db.engine, adopt_legacy=args.command == "adopt-legacy-sqlite")
                result = verify_schema(db.engine)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Connection URLs, SQL parameter values and raw source rows never enter console logs.
        print(json.dumps({"passed": False, "error": type(exc).__name__, "code": str(exc) if isinstance(exc, (ValueError,RuntimeError)) else "DATABASE_OPERATION_FAILED"}))
        raise SystemExit(1) from None
    finally:
        if db is not None: db.close()


if __name__ == "__main__": main()
