#!/usr/bin/env python3
"""
mongo_to_parquet.py — Safe export from MongoDB to Parquet (optimized for nested fields).

- Converts nested fields (snippet, source, tracking, stats_snapshots) into JSON strings.
- Ensures schema consistency across chunks.
- Auto loads .env (MONGO_URI, MONGO_DB, MONGO_COLLECTION)
- Writes in chunks to avoid memory overflow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId, Decimal128, Timestamp, Binary

from config.path_utils import get_export_dir

# Global mode: apply video-specific normalization strictly when True
SAFE_VIDEOS_MODE = False

# =========================================================
# Normalization Helpers
# =========================================================

def normalize_value(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, Decimal128):
        try:
            return float(v.to_decimal())
        except Exception:
            return str(v)
    if isinstance(v, Timestamp):
        return f"{v.time}:{v.inc}"
    if isinstance(v, Binary):
        return v.hex()
    if isinstance(v, dict):
        return {k: normalize_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [normalize_value(x) for x in v]
    return str(v)


def normalize_snapshots_for_parquet(val, mode="full", max_keep=12):
    """Convert stats_snapshots to JSON string (list[dict]) for Parquet friendliness."""
    def _to_obj(x):
        if x is None:
            return []
        if isinstance(x, str):
            try:
                return _to_obj(json.loads(x))
            except Exception:
                return []
        if isinstance(x, dict):
            try:
                return [x[k] for k in sorted(x.keys(), key=lambda z: str(z))]
            except Exception:
                return [x]
        if isinstance(x, (list, tuple)):
            out = []
            for it in x:
                if isinstance(it, str):
                    try:
                        it = json.loads(it)
                    except Exception:
                        pass
                if isinstance(it, dict):
                    out.append({
                        "ts": it.get("ts") or it.get("timestamp") or it.get("time") or it.get("at"),
                        "viewCount": it.get("viewCount") or it.get("views"),
                        "likeCount": it.get("likeCount") or it.get("likes"),
                        "commentCount": it.get("commentCount") or it.get("comments"),
                    })
            return out
        return []

    obj = _to_obj(val)

    # apply slimming / dropping
    if mode == "none":
        obj = []
    elif mode == "slim" and isinstance(obj, list) and max_keep is not None:
        obj = obj[: int(max_keep)]

    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "[]"


def normalize_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize 1 Mongo document to Parquet-friendly dict.

    Behavior is controlled by global SAFE_VIDEOS_MODE:
    - True  → assume `videos` collection; ensure and stringify the 4 fields
              (snippet/source/tracking/stats_snapshots) even if missing.
    - False → generic: only stringify those fields *if present*; do not add
              empty columns for collections that don't use them.
    """
    doc = dict(doc)

    # --- videos-focused normalization ---
    has_video_like_keys = any(k in doc for k in ("snippet", "source", "tracking", "stats_snapshots"))
    do_video_norm = SAFE_VIDEOS_MODE or has_video_like_keys

    if do_video_norm:
        # Ensure subdocs exist only in SAFE mode (avoid polluting other collections)
        if SAFE_VIDEOS_MODE:
            doc.setdefault("snippet", {})
            doc.setdefault("source", {})
            doc.setdefault("tracking", {})
            doc.setdefault("stats_snapshots", [])

        # Normalize tracking.next_poll_after if tracking exists
        tracking = doc.get("tracking")
        if isinstance(tracking, dict):
            if "next_poll_after" in tracking and tracking["next_poll_after"] is None:
                tracking["next_poll_after"] = ""
            elif "next_poll_after" in tracking:
                tracking["next_poll_after"] = str(tracking["next_poll_after"])

        # Stringify nested fields if present
        if "snippet" in doc:
            doc["snippet"] = json.dumps(normalize_value(doc["snippet"]), ensure_ascii=False)
        if "source" in doc:
            doc["source"] = json.dumps(normalize_value(doc["source"]), ensure_ascii=False)
        if "tracking" in doc:
            doc["tracking"] = json.dumps(normalize_value(doc["tracking"]), ensure_ascii=False)
        if "stats_snapshots" in doc:
            doc["stats_snapshots"] = normalize_snapshots_for_parquet(doc.get("stats_snapshots"), mode=os.environ.get("YT_EXPORT_SNAPSHOTS_MODE", getattr(globals(), "_ARG_SNAPSHOTS", "full")), max_keep=int(os.environ.get("YT_EXPORT_SNAPSHOTS_MAX", getattr(globals(), "_ARG_SNAPSHOTS_MAX", 12))) )

    # Flatten the rest generically
    return {k: normalize_value(v) for k, v in doc.items()}


# =========================================================
# Export Logic
# =========================================================

def choose_from_list(items, prompt: str):
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

    parser = argparse.ArgumentParser(description="MongoDB → Parquet export (safe for nested fields)")
    parser.add_argument("--uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db", default=os.getenv("MONGO_DB"))
    parser.add_argument("--collection", "--coll", dest="collection", default=os.getenv("MONGO_COLLECTION"))
    parser.add_argument("--query", type=str, help="MongoDB filter as JSON string")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--chunk", type=int, default=100_000)
    parser.add_argument("--out", type=str)
    parser.add_argument(
        "--safe-videos-mode",
        action="store_true",
        help="Strict videos export: always stringify snippet/source/tracking/stats_snapshots and ensure they exist",
    )
    # Compression & size control
    parser.add_argument(
        "--compression",
        choices=["zstd", "snappy", "gzip", "brotli", "none"],
        default="zstd",
        help="Parquet compression codec (default: zstd)",
    )
    parser.add_argument(
        "--compression-level",
        type=int,
        default=3,
        help="Compression level for codec (zstd/brotli/gzip). Default: 3",
    )
    parser.add_argument(
        "--snapshots",
        choices=["full", "slim", "none"],
        default="full",
        help="Control stats_snapshots export: full = keep all, slim = keep first N, none = drop",
    )
    parser.add_argument(
        "--snapshots-max",
        type=int,
        default=12,
        help="When --snapshots=slim, keep at most N earliest snapshots (default: 12)",
    )
    args = parser.parse_args()

    print(f"[INFO] Connecting to MongoDB: {args.uri}")
    client = MongoClient(args.uri)

    # set global mode flag
    global SAFE_VIDEOS_MODE
    SAFE_VIDEOS_MODE = bool(args.safe_videos_mode)
    print(f"[INFO] SAFE_VIDEOS_MODE = {SAFE_VIDEOS_MODE}")

    # Auto-default for videos: if user did NOT pass --snapshots/--snapshots-max,
    # switch to slim 64 (matches 24h = 64 snapshots plan)
    argv = set(sys.argv[1:])
    user_set_snapshots = any(x in argv for x in {"--snapshots", "--snapshots-max"})
    if SAFE_VIDEOS_MODE and not user_set_snapshots:
        args.snapshots = "slim"
        args.snapshots_max = 64
        print("[INFO] Auto-set snapshots to 'slim' and max_keep=64 for videos mode (no user override)")

    # expose snapshot args for normalize function (via globals to avoid refactor)
    global _ARG_SNAPSHOTS, _ARG_SNAPSHOTS_MAX
    _ARG_SNAPSHOTS = args.snapshots
    _ARG_SNAPSHOTS_MAX = args.snapshots_max

    print(f"[INFO] Snapshots mode = {_ARG_SNAPSHOTS}, max_keep = {_ARG_SNAPSHOTS_MAX}")

    db_name = args.db or choose_from_list(
        [n for n in client.list_database_names() if n not in ("admin", "local", "config")],
        "Select a database:",
    )
    db = client[db_name]

    coll_name = args.collection or choose_from_list(db.list_collection_names(), "Select a collection:")
    coll = db[coll_name]
    print(f"[INFO] Selected {db_name}.{coll_name}")

    query: Dict[str, Any] = {}
    if args.query:
        try:
            query = json.loads(args.query)
        except json.JSONDecodeError:
            raise SystemExit("Invalid JSON for --query")

    cursor = coll.find(query, no_cursor_timeout=True)
    if args.limit:
        cursor = cursor.limit(args.limit)

    export_dir = get_export_dir()
    out_path = Path(args.out or "mongo_export.parquet")
    if not out_path.is_absolute():
        out_path = export_dir / out_path
    out_path = out_path.resolve()

    print(f"[INFO] Writing Parquet → {out_path}")

    # Configure Parquet writer properties
    codec = None if args.compression == "none" else args.compression
    writer_props = pq.ParquetWriter

    writer = None
    total = 0
    processed_docs = 0
    buffer: list[Dict[str, Any]] = []
    all_cols = set()

    # Debug counters for snapshots
    snap_nonempty = 0
    first_snap_example = None

    try:
        for i, doc in enumerate(cursor, 1):
            ndoc = normalize_document(doc)
            buffer.append(ndoc)
            processed_docs += 1

            # --- Debug: count non-empty stats_snapshots ---
            try:
                s = ndoc.get("stats_snapshots")
                if s and s != "[]":
                    arr = json.loads(s)
                    if isinstance(arr, list) and len(arr) > 0:
                        snap_nonempty += 1
                        if first_snap_example is None:
                            first_snap_example = arr[:2]
            except Exception:
                pass
            if len(buffer) >= args.chunk:
                df = pd.DataFrame(buffer)
                all_cols.update(df.columns)
                for c in all_cols:
                    if c not in df.columns:
                        df[c] = None
                df = df[list(sorted(all_cols))]
                table = pa.Table.from_pandas(df, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(
                        out_path,
                        table.schema,
                        compression=codec,
                        use_dictionary=True,
                        compression_level=(args.compression_level if codec in ("zstd", "brotli", "gzip") else None),
                    )
                writer.write_table(table)
                total += len(buffer)
                print(f"[INFO] Wrote chunk, total rows: {total:,}")
                buffer.clear()

        if buffer:
            df = pd.DataFrame(buffer)
            all_cols.update(df.columns)
            for c in all_cols:
                if c not in df.columns:
                    df[c] = None
            df = df[list(sorted(all_cols))]
            table = pa.Table.from_pandas(df, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(
                    out_path,
                    table.schema,
                    compression=codec,
                    use_dictionary=True,
                    compression_level=(args.compression_level if codec in ("zstd", "brotli", "gzip") else None),
                )
            writer.write_table(table)
            total += len(buffer)
            print(f"[INFO] Wrote final chunk, total rows: {total:,}")

    finally:
        cursor.close()
        if writer:
            writer.close()

    print(f"[STATS] Documents processed: {processed_docs:,}")
    print(f"[STATS] Rows with non-empty stats_snapshots: {snap_nonempty:,} / {processed_docs:,}")
    if first_snap_example is not None:
        print(f"[STATS] Example snapshots (first row): {first_snap_example}")

    print(f"[DONE] Exported {total:,} rows → {out_path}")


if __name__ == "__main__":
    main()
