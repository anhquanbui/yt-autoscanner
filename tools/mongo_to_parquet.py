#!/usr/bin/env python3
"""
mongo_to_parquet.py — Export MongoDB collection to Parquet for ML use.

✔ Auto-loads .env (MONGO_URI, MONGO_DB, MONGO_COLLECTION)
✔ Defaults output to ../data_export/ under project root
✔ Interactive DB & collection chooser if not provided
✔ BSON-safe normalization (ObjectId, Decimal128, etc.)
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict
import pandas as pd

# --- Load .env if available ---
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(dotenv_path=None): ...

# --- MongoDB imports ---
try:
    from pymongo import MongoClient
    from bson import ObjectId, Decimal128, Timestamp, Binary
except ImportError as e:
    raise SystemExit("Please install dependencies:\n  pip install pymongo python-dotenv pyarrow") from e


# === Utility functions ===
def choose_from_list(items, prompt: str):
    """Simple interactive chooser."""
    if not items:
        raise SystemExit(f"No options available for: {prompt}")
    print(f"\n{prompt}")
    for i, name in enumerate(items, 1):
        print(f"  [{i}] {name}")
    while True:
        c = input(f"Enter number (1-{len(items)}): ").strip()
        if c.isdigit() and 1 <= int(c) <= len(items):
            return items[int(c) - 1]
        print("Invalid choice. Try again.")


def normalize_value(v: Any) -> Any:
    """Convert BSON-specific types to JSON/Parquet-safe formats."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, Decimal128):
        try:
            return float(v.to_decimal())  # type: ignore[attr-defined]
        except Exception:
            return str(v)
    if isinstance(v, Timestamp):
        return f"{v.time}:{v.inc}"  # type: ignore[attr-defined]
    if isinstance(v, Binary):
        return v.hex()  # type: ignore[attr-defined]
    if isinstance(v, dict):
        return {k: normalize_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [normalize_value(x) for x in v]
    return str(v)


def normalize_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: normalize_value(v) for k, v in doc.items()}


# === Main ===
def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Export MongoDB collection → Parquet (for ML).")
    parser.add_argument("--uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.getenv("MONGO_DB"))
    parser.add_argument("--collection", "--coll", dest="collection", default=os.getenv("MONGO_COLLECTION"))
    parser.add_argument("--query", type=str, help="MongoDB filter as JSON string, e.g. '{\"status\":\"complete\"}'")
    parser.add_argument("--limit", type=int, help="Limit number of documents")
    parser.add_argument("--out", type=str, help="Optional output file path (default: ../data_export/mongo_export.parquet)")
    args = parser.parse_args()

    print(f"[INFO] Connecting to MongoDB: {args.uri}")
    client = MongoClient(args.uri)

    # Select DB
    if args.db:
        db_name = args.db
        print(f"[INFO] Using database: {db_name}")
    else:
        db_name = choose_from_list(
            [n for n in client.list_database_names() if n not in ("admin", "local", "config")],
            "Select a database:"
        )
    db = client[db_name]

    # Select collection
    if args.collection:
        coll_name = args.collection
        print(f"[INFO] Using collection: {coll_name}")
    else:
        coll_name = choose_from_list(db.list_collection_names(), "Select a collection:")
    coll = db[coll_name]

    # Filter
    query = {}
    if args.query:
        try:
            query = json.loads(args.query)
        except json.JSONDecodeError:
            raise SystemExit("Invalid JSON for --query")

    print(f"[INFO] Query: {query}")
    cursor = coll.find(query)
    if args.limit:
        cursor = cursor.limit(args.limit)
        print(f"[INFO] Limiting to {args.limit} docs")

    # Fetch documents
    docs = []
    for i, d in enumerate(cursor, 1):
        docs.append(normalize_document(d))
        if i % 10000 == 0:
            print(f"[INFO] Loaded {i:,} documents...")
    print(f"[INFO] Total loaded: {len(docs):,}")

    if not docs:
        print("[WARN] No documents found.")
        return

    df = pd.DataFrame(docs)

    # Output to ../data_export/
    project_root = Path(__file__).resolve().parents[1]
    export_dir = project_root.parent / "data_export"
    export_dir.mkdir(parents=True, exist_ok=True)
    default_out = export_dir / "mongo_export.parquet"

    out_path = Path(args.out).resolve() if args.out else default_out
    print(f"[INFO] Writing Parquet → {out_path}")

    df.to_parquet(out_path, index=False)
    print(f"[DONE] Exported {len(df):,} rows to {out_path}")


if __name__ == "__main__":
    main()
