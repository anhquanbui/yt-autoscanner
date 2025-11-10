#!/usr/bin/env python3
"""
mongo_to_parquet.py — Chunked Export of MongoDB collection to Parquet for ML use.

✔ Auto-load .env (MONGO_URI, MONGO_DB, MONGO_COLLECTION)
✔ Stream export by chunks (no memory overflow)
✔ Default output directory: ../data_export/
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(dotenv_path=None): ...

try:
    from pymongo import MongoClient
    from bson import ObjectId, Decimal128, Timestamp, Binary
except ImportError as e:
    raise SystemExit("Please install:\n  pip install pymongo python-dotenv pyarrow pandas") from e


def normalize_value(v: Any) -> Any:
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


def choose_from_list(items, prompt: str):
    if not items:
        raise SystemExit(f"No options for {prompt}")
    print(f"\n{prompt}")
    for i, name in enumerate(items, 1):
        print(f"  [{i}] {name}")
    while True:
        c = input(f"Enter number (1-{len(items)}): ").strip()
        if c.isdigit() and 1 <= int(c) <= len(items):
            return items[int(c) - 1]
        print("Invalid choice, try again.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Chunked export MongoDB → Parquet (safe for large data)")
    parser.add_argument("--uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.getenv("MONGO_DB"))
    parser.add_argument("--collection", "--coll", dest="collection", default=os.getenv("MONGO_COLLECTION"))
    parser.add_argument("--query", type=str, help="MongoDB filter JSON string")
    parser.add_argument("--limit", type=int, help="Limit number of documents")
    parser.add_argument("--chunk", type=int, default=100000, help="Chunk size per write (default 100k)")
    parser.add_argument("--out", type=str, help="Output path (default: ../data_export/mongo_export.parquet)")
    args = parser.parse_args()

    print(f"[INFO] Connecting to MongoDB: {args.uri}")
    client = MongoClient(args.uri)

    # Select DB
    if args.db:
        db_name = args.db
    else:
        db_name = choose_from_list(
            [n for n in client.list_database_names() if n not in ("admin", "local", "config")],
            "Select a database:"
        )
    db = client[db_name]

    # Select collection
    if args.collection:
        coll_name = args.collection
    else:
        coll_name = choose_from_list(db.list_collection_names(), "Select a collection:")
    coll = db[coll_name]
    print(f"[INFO] Selected {db_name}.{coll_name}")

    # Filter
    query = {}
    if args.query:
        try:
            query = json.loads(args.query)
        except json.JSONDecodeError:
            raise SystemExit("Invalid JSON for --query")

    print(f"[INFO] Query: {query}")
    cursor = coll.find(query, no_cursor_timeout=True)
    if args.limit:
        cursor = cursor.limit(args.limit)

    # Prepare output path
    project_root = Path(__file__).resolve().parents[1]
    export_dir = project_root.parent / "data_export"
    export_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out).resolve() if args.out else export_dir / "mongo_export.parquet"

    # Parquet writer setup
    writer = None
    total = 0
    buffer = []

    try:
        for i, doc in enumerate(cursor, 1):
            buffer.append(normalize_document(doc))
            if len(buffer) >= args.chunk:
                df = pd.DataFrame(buffer)
                table = pa.Table.from_pandas(df)
                if writer is None:
                    writer = pq.ParquetWriter(out_path, table.schema)
                writer.write_table(table)
                total += len(buffer)
                print(f"[INFO] Wrote chunk {total:,} rows → {out_path}")
                buffer.clear()
        # Final flush
        if buffer:
            df = pd.DataFrame(buffer)
            table = pa.Table.from_pandas(df)
            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema)
            writer.write_table(table)
            total += len(buffer)
            print(f"[INFO] Wrote final chunk ({len(buffer):,} rows)")

    finally:
        cursor.close()
        if writer:
            writer.close()

    print(f"[DONE] Exported total {total:,} rows → {out_path}")


if __name__ == "__main__":
    main()
