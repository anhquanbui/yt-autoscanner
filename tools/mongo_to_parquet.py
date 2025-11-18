#!/usr/bin/env python3
"""
mongo_to_parquet.py — Safe, schema-stable export from MongoDB → Parquet.

✔ Supports nested Mongo documents (snippet/source/tracking/stats_snapshots)
✔ Converts nested structures into JSON strings for Parquet friendliness
✔ Works in chunked mode to avoid RAM overflow
✔ Unified env loader (config.env) — consistent with discover_once / track_once
✔ Optional SAFE_VIDEOS_MODE for strongly-normalized "videos" collection
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
from pymongo import MongoClient
from bson import ObjectId, Decimal128, Timestamp, Binary

from config.env import load_env, get_env
from config.path_utils import get_export_dir

# Global mode flag for strong normalization
SAFE_VIDEOS_MODE = False


# =====================================================================
#                         NORMALIZATION HELPERS
# =====================================================================

def normalize_value(v: Any) -> Any:
    """
    Normalize Python/Mongo values into Parquet-friendly types.

    - Basic primitives returned as-is.
    - ObjectId → string
    - Decimal128 → float (fallback string)
    - Timestamp → "time:inc"
    - Binary → hex string
    - dict/list → recursively normalized
    """
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
    """
    Convert stats_snapshots to a compact JSON-encoded list suitable for Parquet.

    Modes:
      - full : keep all snapshots
      - slim : keep only the first N snapshots
      - none : remove snapshots entirely
    """
    def _to_obj(x):
        if x is None:
            return []
        if isinstance(x, str):
            try:
                return _to_obj(json.loads(x))
            except Exception:
                return []
        if isinstance(x, dict):
            # Snapshot stored as dict-of-dicts
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

    # apply slimming/drop rules
    if mode == "none":
        obj = []
    elif mode == "slim":
        obj = obj[:int(max_keep)]

    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "[]"


def normalize_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a single MongoDB document for Parquet output.

    Behavior:
      - SAFE_VIDEOS_MODE=True → enforce strict structure for videos collection
      - Otherwise:
          stringify nested fields only if present
          avoid injecting empty dicts
    """
    doc = dict(doc)

    has_video_like_keys = any(k in doc for k in ("snippet", "source", "tracking", "stats_snapshots"))
    do_video_norm = SAFE_VIDEOS_MODE or has_video_like_keys

    if do_video_norm:
        # Ensure minimal structure in strict mode
        if SAFE_VIDEOS_MODE:
            doc.setdefault("snippet", {})
            doc.setdefault("source", {})
            doc.setdefault("tracking", {})
            doc.setdefault("stats_snapshots", [])

        # Normalize tracking.next_poll_after (None → empty string)
        tracking = doc.get("tracking")
        if isinstance(tracking, dict):
            if tracking.get("next_poll_after") is None:
                tracking["next_poll_after"] = ""
            else:
                tracking["next_poll_after"] = str(tracking["next_poll_after"])

        # Convert nested dicts to JSON strings
        if "snippet" in doc:
            doc["snippet"] = json.dumps(normalize_value(doc["snippet"]), ensure_ascii=False)
        if "source" in doc:
            doc["source"] = json.dumps(normalize_value(doc["source"]), ensure_ascii=False)
        if "tracking" in doc:
            doc["tracking"] = json.dumps(normalize_value(doc["tracking"]), ensure_ascii=False)
        if "stats_snapshots" in doc:
            mode = os.environ.get("YT_EXPORT_SNAPSHOTS_MODE", globals().get("_ARG_SNAPSHOTS", "full"))
            max_keep = int(os.environ.get("YT_EXPORT_SNAPSHOTS_MAX", globals().get("_ARG_SNAPSHOTS_MAX", 12)))
            doc["stats_snapshots"] = normalize_snapshots_for_parquet(doc.get("stats_snapshots"), mode, max_keep)

    # Flatten remaining fields normally
    return {k: normalize_value(v) for k, v in doc.items()}


# =====================================================================
#                             EXPORT LOGIC
# =====================================================================

def choose_from_list(items, prompt: str):
    """Simple interactive selector for database/collection choice."""
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
    # Unified env loader (replaces load_dotenv)
    load_env()

    parser = argparse.ArgumentParser(description="MongoDB → Parquet exporter (schema-stable, safe for nested fields)")

    parser.add_argument("--uri", default=get_env("MONGO_URI", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--db", default=get_env("MONGO_DB"))
    parser.add_argument("--collection", "--coll", dest="collection", default=get_env("MONGO_COLLECTION"))
    parser.add_argument("--query", type=str)

    parser.add_argument("--limit", type=int)
    parser.add_argument("--chunk", type=int, default=100_000)

    parser.add_argument("--out", type=str)

    parser.add_argument(
        "--safe-videos-mode",
        action="store_true",
        help="Force videos schema: always stringify snippet/source/tracking/stats_snapshots",
    )

    parser.add_argument("--snapshots", choices=["full", "slim", "none"], default="full")
    parser.add_argument("--snapshots-max", type=int, default=12)

    parser.add_argument("--compression", choices=["zstd", "snappy", "gzip", "brotli", "none"], default="zstd")
    parser.add_argument("--compression-level", type=int, default=3)

    args = parser.parse_args()

    print(f"[INFO] Connecting to MongoDB: {args.uri}")
    client = MongoClient(args.uri)

    # Set global video-mode flag
    global SAFE_VIDEOS_MODE
    SAFE_VIDEOS_MODE = bool(args.safe_videos_mode)
    print(f"[INFO] SAFE_VIDEOS_MODE = {SAFE_VIDEOS_MODE}")

    # Auto-adjust snapshots mode for videos if user did not override
    argv = set(sys.argv[1:])
    user_set_snapshots = any(x in argv for x in {"--snapshots", "--snapshots-max"})
    if SAFE_VIDEOS_MODE and not user_set_snapshots:
        args.snapshots = "slim"
        args.snapshots_max = 64
        print("[INFO] Auto-set snapshots to 'slim' (max_keep=64) for videos mode")

    global _ARG_SNAPSHOTS, _ARG_SNAPSHOTS_MAX
    _ARG_SNAPSHOTS = args.snapshots
    _ARG_SNAPSHOTS_MAX = args.snapshots_max

    print(f"[INFO] Snapshots mode = {_ARG_SNAPSHOTS}, max_keep = {_ARG_SNAPSHOTS_MAX}")

    # Database selection
    db_name = args.db or choose_from_list(
        [n for n in client.list_database_names() if n not in ("admin", "local", "config")],
        "Select a database:"
    )
    db = client[db_name]

    # Collection selection
    coll_name = args.collection or choose_from_list(db.list_collection_names(), "Select a collection:")
    coll = db[coll_name]
    print(f"[INFO] Selected collection: {db_name}.{coll_name}")

    query: Dict[str, Any] = {}
    if args.query:
        try:
            query = json.loads(args.query)
        except json.JSONDecodeError:
            raise SystemExit("Invalid JSON for --query")

    cursor = coll.find(query, no_cursor_timeout=True)
    if args.limit:
        cursor = cursor.limit(args.limit)

    # Determine output path via export dir
    export_dir = get_export_dir()
    out_path = Path(args.out or "mongo_export.parquet")
    if not out_path.is_absolute():
        out_path = export_dir / out_path
    out_path = out_path.resolve()

    print(f"[INFO] Writing Parquet → {out_path}")

    codec = None if args.compression == "none" else args.compression

    writer = None
    processed_docs = 0
    total_rows = 0
    buffer: list[Dict[str, Any]] = []
    all_cols = set()

    # Debug counters for snapshots
    snap_nonempty = 0
    first_snap_example = None

    try:
        for doc in cursor:
            ndoc = normalize_document(doc)
            buffer.append(ndoc)
            processed_docs += 1

            # debug snapshot stats
            try:
                s = ndoc.get("stats_snapshots")
                if s and s != "[]":
                    arr = json.loads(s)
                    if isinstance(arr, list) and arr:
                        snap_nonempty += 1
                        if first_snap_example is None:
                            first_snap_example = arr[:2]
            except Exception:
                pass

            # Write chunk
            if len(buffer) >= args.chunk:
                df = pd.DataFrame(buffer)
                all_cols.update(df.columns)

                # ensure stable schema
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
                total_rows += len(buffer)
                print(f"[INFO] Wrote chunk — total rows: {total_rows:,}")
                buffer.clear()

        # Final flush
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
            total_rows += len(buffer)
            print(f"[INFO] Wrote final chunk — total rows: {total_rows:,}")

    finally:
        cursor.close()
        if writer:
            writer.close()

    print(f"[STATS] Documents processed: {processed_docs:,}")
    print(f"[STATS] Rows with non-empty stats_snapshots: {snap_nonempty:,} / {processed_docs:,}")
    if first_snap_example is not None:
        print(f"[STATS] Example snapshots: {first_snap_example}")

    print(f"[DONE] Export finished → {out_path}")


if __name__ == "__main__":
    main()
