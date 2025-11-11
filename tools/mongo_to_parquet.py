#!/usr/bin/env python3
"""
mongo_to_parquet.py — Chunked export MongoDB collection → Parquet for ML.

- Auto loads .env (MONGO_URI, MONGO_DB, MONGO_COLLECTION)
- Uses centralized export dir from config.path_utils
  (EXPORT_DIR / OUTPUT_DIR / data_export)
- Writes in chunks to avoid memory overflow
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
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId, Decimal128, Timestamp, Binary

from config.path_utils import get_export_dir


def normalize_value(v: Any) -> Any:
    """Normalize BSON/JSON values into Parquet-friendly types."""
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
    # Make a shallow copy so we don't mutate the original Mongo document
    doc = dict(doc)

    # Special handling: tracking.next_poll_after should always be a string
    tracking = doc.get("tracking")
    if isinstance(tracking, dict):
        # If field exists but is None, normalize to empty string
        if "next_poll_after" in tracking and tracking["next_poll_after"] is None:
            tracking["next_poll_after"] = ""
        # You can also ensure it is str if it's some other type
        elif "next_poll_after" in tracking and tracking["next_poll_after"] is not None:
            tracking["next_poll_after"] = str(tracking["next_poll_after"])

    return {k: normalize_value(v) for k, v in doc.items()}



def choose_from_list(items, prompt: str):
    """Interactive menu to choose an item from a list."""
    if not items:
        raise SystemExit(f"No options for {prompt}")
    print(f"\n{prompt}")
    for i, name in enumerate(items, 1):
        print(f"  [{i}] {name}")
    while True:
        choice = input(f"Enter number (1-{len(items)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            return items[int(choice) - 1]
        print("Invalid choice, try again.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Chunked export MongoDB → Parquet (safe for large data)."
    )
    parser.add_argument(
        "--uri",
        default=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        help="MongoDB connection URI (default: env MONGO_URI or localhost)",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MONGO_DB"),
        help="Database name (default: env MONGO_DB, otherwise choose interactively).",
    )
    parser.add_argument(
        "--collection",
        "--coll",
        dest="collection",
        default=os.getenv("MONGO_COLLECTION"),
        help="Collection name (default: env MONGO_COLLECTION, otherwise choose interactively).",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="MongoDB filter as JSON string, e.g. '{\"tracking.status\":\"complete\"}'",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of documents (optional).",
    )
    parser.add_argument(
        "--chunk",
        type=int,
        default=100_000,
        help="Chunk size per write (default: 100000).",
    )
    parser.add_argument(
        "--out",
        type=str,
        help="Output filename (relative to EXPORT_DIR). Default: mongo_export.parquet",
    )
    args = parser.parse_args()

    print(f"[INFO] Connecting to MongoDB: {args.uri}")
    client = MongoClient(args.uri)

    # --- Select database ---
    if args.db:
        db_name = args.db
    else:
        db_name = choose_from_list(
            [n for n in client.list_database_names() if n not in ("admin", "local", "config")],
            "Select a database:",
        )
    db = client[db_name]

    # --- Select collection ---
    if args.collection:
        coll_name = args.collection
    else:
        coll_name = choose_from_list(db.list_collection_names(), "Select a collection:")
    coll = db[coll_name]
    print(f"[INFO] Selected {db_name}.{coll_name}")

    # --- Build query ---
    query: Dict[str, Any] = {}
    if args.query:
        try:
            query = json.loads(args.query)
        except json.JSONDecodeError:
            raise SystemExit("Invalid JSON for --query")

    print(f"[INFO] Query: {query}")
    cursor = coll.find(query, no_cursor_timeout=True)
    if args.limit:
        cursor = cursor.limit(args.limit)
        print(f"[INFO] Limiting to {args.limit} documents")

    # --- Resolve export directory via path_utils ---
    export_dir = get_export_dir()  # uses EXPORT_DIR / OUTPUT_DIR / data_export
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = export_dir / out_path
        out_path = out_path.resolve()
    else:
        out_path = (export_dir / "mongo_export.parquet").resolve()

    print(f"[INFO] Export directory: {export_dir}")
    print(f"[INFO] Writing Parquet → {out_path}")

    # --- Chunked write ---
    writer: pq.ParquetWriter | None = None
    total = 0
    buffer: list[Dict[str, Any]] = []

    try:
        for i, doc in enumerate(cursor, 1):
            buffer.append(normalize_document(doc))
            if len(buffer) >= args.chunk:
                df = pd.DataFrame(buffer)
                table = pa.Table.from_pandas(df)
                if writer is None:
                    # First chunk: create writer with this schema
                    writer = pq.ParquetWriter(out_path, table.schema)
                else:
                    # Next chunks: cast table to the first chunk's schema
                    table = table.cast(writer.schema)
                writer.write_table(table)
                total += len(buffer)
                print(f"[INFO] Wrote chunk, total rows: {total:,}")
                buffer.clear()

        # Final flush
        if buffer:
            df = pd.DataFrame(buffer)
            table = pa.Table.from_pandas(df)
            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema)
            else:
                table = table.cast(writer.schema)
            writer.write_table(table)
            total += len(buffer)
            print(f"[INFO] Wrote final chunk, total rows: {total:,}")

    finally:
        cursor.close()
        if writer:
            writer.close()

    print(f"[DONE] Exported {total:,} rows → {out_path}")


if __name__ == "__main__":
    main()
