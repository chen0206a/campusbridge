"""Local, versioned JSON/JSONL knowledge import. Never contacts a model."""
import argparse
import json
from pathlib import Path

from campusbridge.db import Database
from campusbridge.retrieval import SQLiteKnowledge, bundled_documents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--file", type=Path, help="Array JSON or one DocumentInput per JSONL line; omit for original demo corpus")
    args = parser.parse_args()
    if args.file:
        if args.file.stat().st_size > 2_000_000:
            parser.error("Import file exceeds 2 MB")
        value = args.file.read_text(encoding="utf-8-sig")
        documents = [json.loads(line) for line in value.splitlines() if line.strip()] if args.file.suffix == ".jsonl" else json.loads(value)
    else:
        documents = bundled_documents()
    db = Database(args.database)
    try:
        print(json.dumps(SQLiteKnowledge(db).import_documents(documents), ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
