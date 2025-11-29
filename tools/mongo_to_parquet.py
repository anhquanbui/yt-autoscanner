#!/usr/bin/env python3
"""
mongo_to_parquet.py — Safe, schema-stable export from MongoDB → Parquet.

Main goals
----------
✔ Export large MongoDB collections to Parquet without blowing up RAM.
✔ Keep the schema stable across chunks (same columns in all row groups).
✔ Normalize Mongo-specific types (ObjectId, Decimal128, Timestamp, Binary).
✔ Handle nested documents (snippet / source / tracking / stats_snapshots) in a
  Parquet-friendly way by encoding them as JSON strings.
✔ Use shared environment loader (config.env) so it behaves like discover_once / track_once.
✔ Optional SAFE_VIDEOS_MODE for stronger normalization for the `videos` collection.

Typical usage
-------------
    python mongo_to_parquet.py \
        --uri "mongodb://127.0.0.1:27017" \
        --db ytscan \
        --collection videos \
        --safe-videos-mode \
        --snapshots slim \
        --snapshots-max 64 \
        --out videos_export.parquet
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

# Global mode flag:
# - False: behave generically (only normalize fields that exist).
# - True : assume "videos-like" schema and enforce strict normalization
#          for snippet/source/tracking/stats_snapshots.
SAFE_VIDEOS_MODE = False


# =====================================================================
#                         NORMALIZATION HELPERS
# =====================================================================

def normalize_value(v: Any) -> Any:
    """
    Normalize Python / MongoDB values into Parquet-friendly types.

    Behavior
    --------
    - Basic primitives (str, int, float, bool, None) are returned as-is.
    - ObjectId   → string (hex)
    - Decimal128 → float (with string fallback if conversion fails)
    - Timestamp  → string "time:inc"
    - Binary     → hex string
    - dict       → recursively normalized dict
    - list/tuple → recursively normalized list
    - Other      → generic str() representation

    This function is deliberately conservative: it aims to avoid any
    non-serializable or exotic types getting through to Pandas / PyArrow.
    """
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v

    if isinstance(v, ObjectId):
        return str(v)

    if isinstance(v, Decimal128):
        try:
            return float(v.to_decimal())
        except Exception:
            # If something goes wrong, keep the value as a string so we don't
            # lose the information entirely.
            return str(v)

    if isinstance(v, Timestamp):
        # BSON Timestamp has (time, inc). We preserve both as a compact string.
        return f"{v.time}:{v.inc}"

    if isinstance(v, Binary):
        # Represent binary data as hex to keep it Parquet-safe.
        return v.hex()

    if isinstance(v, dict):
        # Recursively normalize dict values.
        return {k: normalize_value(x) for k, x in v.items()}

    if isinstance(v, (list, tuple)):
        # Recursively normalize each element.
        return [normalize_value(x) for x in v]

    # Fallback: string representation of any unknown type.
    return str(v)


def normalize_snapshots_for_parquet(val, mode="full", max_keep=12):
    """
    Convert `stats_snapshots` into a compact JSON-encoded list suitable for Parquet.

    Parameters
    ----------
    val:
        Raw `stats_snapshots` field from MongoDB. Can be:
        - list/tuple of dicts
        - dict-of-dicts
        - pre-encoded JSON string
        - None / unexpected types

    mode:
        - "full": keep all snapshots.
        - "slim": keep only the first `max_keep` snapshots.
        - "none": drop snapshots entirely (encode as empty list).

    max_keep:
        Maximum number of snapshots when `mode="slim"`.

    Output
    ------
    A JSON string representing a list of snapshots. Each snapshot is normalized
    to a small dict with keys:
        - ts          (timestamp-ish)
        - viewCount
        - likeCount
        - commentCount
    """

    def _to_obj(x):
        """
        Convert raw snapshots into a standard Python list-of-dicts form
        before JSON encoding.
        """
        if x is None:
            return []

        # If we receive a JSON string, attempt to parse it first.
        if isinstance(x, str):
            try:
                return _to_obj(json.loads(x))
            except Exception:
                return []

        # If the stored value is a dict-of-dicts (e.g. keyed by index or time)
        # convert it to a list sorted by key.
        if isinstance(x, dict):
            try:
                return [x[k] for k in sorted(x.keys(), key=lambda z: str(z))]
            except Exception:
                # Fallback: treat the entire dict as a single snapshot.
                return [x]

        if isinstance(x, (list, tuple)):
            out = []
            for it in x:
                # If each element is a JSON string, parse it.
                if isinstance(it, str):
                    try:
                        it = json.loads(it)
                    except Exception:
                        pass

                if isinstance(it, dict):
                    out.append(
                        {
                            "ts": it.get("ts")
                                  or it.get("timestamp")
                                  or it.get("time")
                                  or it.get("at"),
                            "viewCount": it.get("viewCount") or it.get("views"),
                            "likeCount": it.get("likeCount") or it.get("likes"),
                            "commentCount": it.get("commentCount") or it.get("comments"),
                        }
                    )
            return out

        # Anything else → treat as "no snapshots".
        return []

    # Normalize to a Python list first.
    obj = _to_obj(val)

    # Apply slimming / drop rules
    if mode == "none":
        obj = []
    elif mode == "slim":
        obj = obj[:int(max_keep)]

    try:
        # Compact JSON (no extra whitespace), UTF-8 safe.
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        # Fallback: encode as empty list if something unexpected occurs.
        return "[]"


def normalize_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a single MongoDB document for Parquet output.

    Behavior
    --------
    - If SAFE_VIDEOS_MODE is True:
        * Enforce minimal structure:
            snippet, source, tracking, stats_snapshots always present.
        * Flatten those nested structures into JSON string fields.
        * Normalize tracking.next_poll_after to a string (or empty string).
        * Transform stats_snapshots using normalize_snapshots_for_parquet().

    - If SAFE_VIDEOS_MODE is False:
        * Detect "video-like" documents by checking for keys:
            snippet, source, tracking, stats_snapshots.
        * For such documents, still perform the same flattening logic, but
          only for fields that actually exist.
        * Do not inject empty dicts for missing fields.

    - All remaining fields:
        * Are passed through normalize_value().
        * `ml_flags` is JSON-encoded, unless already a string.
        * `latest_stats_ts` is stringified for stability across chunks.

    The goal is to end up with a flat dict of scalar/JSON-string fields that
    Pandas / PyArrow can handle consistently across chunks.
    """
    doc = dict(doc)

    # Detect whether this document looks like a "videos" document.
    has_video_like_keys = any(k in doc for k in ("snippet", "source", "tracking", "stats_snapshots"))
    do_video_norm = SAFE_VIDEOS_MODE or has_video_like_keys

    if do_video_norm:
        # In strict mode, ensure these keys exist so the schema is predictable.
        if SAFE_VIDEOS_MODE:
            doc.setdefault("snippet", {})
            doc.setdefault("source", {})
            doc.setdefault("tracking", {})
            doc.setdefault("stats_snapshots", [])

        # Normalize tracking.next_poll_after:
        # - None   → "" (empty string)
        # - others → str(...)
        tracking = doc.get("tracking")
        if isinstance(tracking, dict):
            if tracking.get("next_poll_after") is None:
                tracking["next_poll_after"] = ""
            else:
                tracking["next_poll_after"] = str(tracking["next_poll_after"])

        # Flatten nested dicts → JSON strings for Parquet.
        if "snippet" in doc:
            doc["snippet"] = json.dumps(normalize_value(doc["snippet"]), ensure_ascii=False)
        if "source" in doc:
            doc["source"] = json.dumps(normalize_value(doc["source"]), ensure_ascii=False)
        if "tracking" in doc:
            doc["tracking"] = json.dumps(normalize_value(doc["tracking"]), ensure_ascii=False)
        if "stats_snapshots" in doc:
            # Mode and max_keep can come from env OR CLI args (via globals).
            mode = os.environ.get("YT_EXPORT_SNAPSHOTS_MODE", globals().get("_ARG_SNAPSHOTS", "full"))
            max_keep = int(os.environ.get("YT_EXPORT_SNAPSHOTS_MAX", globals().get("_ARG_SNAPSHOTS_MAX", 12)))
            doc["stats_snapshots"] = normalize_snapshots_for_parquet(
                doc.get("stats_snapshots"),
                mode,
                max_keep,
            )

    # Normalize ml_flags into a JSON string so that its structure is fixed
    # and does not expand into many Parquet columns.
    if "ml_flags" in doc and not isinstance(doc["ml_flags"], str):
        try:
            doc["ml_flags"] = json.dumps(normalize_value(doc["ml_flags"]), ensure_ascii=False)
        except Exception:
            # Fallback: best-effort string representation.
            doc["ml_flags"] = str(doc["ml_flags"])

    # latest_stats_ts can be a datetime or some BSON type — make it a string
    # to avoid mixed type issues across chunks.
    if "latest_stats_ts" in doc and doc["latest_stats_ts"] is not None:
        doc["latest_stats_ts"] = str(doc["latest_stats_ts"])

    # Finally, flatten all remaining fields using normalize_value.
    return {k: normalize_value(v) for k, v in doc.items()}


# =====================================================================
#                             EXPORT LOGIC
# =====================================================================

def choose_from_list(items, prompt: str):
    """
    Simple interactive selector for database / collection choice.

    This is used when --db or --collection are not provided via CLI or env.

    Parameters
    ----------
    items:
        List of candidate names.
    prompt:
        Human-readable prompt displayed before listing options.

    Returns
    -------
    str
        The selected item from `items`.
    """
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
    """
    CLI entrypoint for MongoDB → Parquet export.

    High-level flow
    ---------------
    1. Load environment variables via config.env (unified loader).
    2. Parse CLI arguments (URI, db, collection, query, chunk size, etc.).
    3. Connect to MongoDB (single client) and resolve DB & collection.
    4. Stream documents in chunks:
         - Normalize each document with `normalize_document()`.
         - Append to a buffer until chunk size is reached.
         - Convert to DataFrame → PyArrow Table → write to Parquet.
    5. Ensure a stable schema across chunks:
         - Track global set of columns.
         - For each chunk, add missing columns as None.
         - Align to the writer's schema before writing.
    6. Print export stats (rows processed, snapshot coverage, example snapshot).

    This script is safe to use for large collections as it never loads the
    entire cursor into memory at once.
    """
    # Unified env loader (replaces direct load_dotenv calls).
    load_env()

    parser = argparse.ArgumentParser(
        description="MongoDB → Parquet exporter (schema-stable, safe for nested fields)"
    )

    # Connection / selection
    parser.add_argument("--uri", default=get_env("MONGO_URI", "mongodb://127.0.0.1:27017"))
    parser.add_argument("--db", default=get_env("MONGO_DB"))
    parser.add_argument("--collection", "--coll", dest="collection", default=get_env("MONGO_COLLECTION"))
    parser.add_argument("--query", type=str, help="Optional Mongo query as JSON string")

    # Limits / chunking
    parser.add_argument("--limit", type=int, help="Optional max number of documents to export")
    parser.add_argument("--chunk", type=int, default=100_000, help="Chunk size for streaming export")

    # Output path
    parser.add_argument("--out", type=str, help="Output Parquet file name (default: mongo_export.parquet)")

    # Video-specific normalization mode
    parser.add_argument(
        "--safe-videos-mode",
        action="store_true",
        help="Force videos schema: always stringify snippet/source/tracking/stats_snapshots",
    )

    # Snapshots behavior
    parser.add_argument(
        "--snapshots",
        choices=["full", "slim", "none"],
        default="full",
        help="How to handle stats_snapshots (default: full)",
    )
    parser.add_argument(
        "--snapshots-max",
        type=int,
        default=12,
        help="Max snapshots to keep when --snapshots=slim (default: 12)",
    )

    # Compression settings for Parquet
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
        help="Compression level for zstd/brotli/gzip (default: 3)",
    )

    args = parser.parse_args()

    print(f"[INFO] Connecting to MongoDB: {args.uri}")
    client = MongoClient(args.uri)

    # Set global video-mode flag from CLI.
    global SAFE_VIDEOS_MODE
    SAFE_VIDEOS_MODE = bool(args.safe_videos_mode)
    print(f"[INFO] SAFE_VIDEOS_MODE = {SAFE_VIDEOS_MODE}")

    # If we are in "videos mode" and the user did not explicitly set
    # snapshots behavior, apply a more aggressive default:
    #   - mode = 'slim'
    #   - max_keep = 64
    argv = set(sys.argv[1:])
    user_set_snapshots = any(x in argv for x in {"--snapshots", "--snapshots-max"})
    if SAFE_VIDEOS_MODE and not user_set_snapshots:
        args.snapshots = "slim"
        args.snapshots_max = 64
        print("[INFO] Auto-set snapshots to 'slim' (max_keep=64) for videos mode")

    # Make snapshot arguments visible inside normalize_document().
    global _ARG_SNAPSHOTS, _ARG_SNAPSHOTS_MAX
    _ARG_SNAPSHOTS = args.snapshots
    _ARG_SNAPSHOTS_MAX = args.snapshots_max

    print(f"[INFO] Snapshots mode = {_ARG_SNAPSHOTS}, max_keep = {_ARG_SNAPSHOTS_MAX}")

    # Database selection: if --db not provided, let the user pick interactively
    db_name = args.db or choose_from_list(
        [n for n in client.list_database_names() if n not in ("admin", "local", "config")],
        "Select a database:",
    )
    db = client[db_name]

    # Collection selection: same logic as DB
    coll_name = args.collection or choose_from_list(
        db.list_collection_names(),
        "Select a collection:",
    )
    coll = db[coll_name]
    print(f"[INFO] Selected collection: {db_name}.{coll_name}")

    # Optional filter query (JSON string → dict)
    query: Dict[str, Any] = {}
    if args.query:
        try:
            query = json.loads(args.query)
        except json.JSONDecodeError:
            raise SystemExit("Invalid JSON for --query")

    cursor = coll.find(query, no_cursor_timeout=True)
    if args.limit:
        cursor = cursor.limit(args.limit)

    # Determine output path using the unified export directory helper.
    export_dir = get_export_dir()
    out_path = Path(args.out or "mongo_export.parquet")
    if not out_path.is_absolute():
        out_path = export_dir / out_path
    out_path = out_path.resolve()

    print(f"[INFO] Writing Parquet → {out_path}")

    # Map "none" to no compression in the Parquet writer.
    codec = None if args.compression == "none" else args.compression

    writer = None
    writer_cols = None  # frozen column order recorded from the first chunk
    processed_docs = 0
    total_rows = 0
    buffer: list[Dict[str, Any]] = []
    all_cols = set()

    # Debug counters for stats_snapshots coverage
    snap_nonempty = 0
    first_snap_example = None

    try:
        for doc in cursor:
            # Normalize each document before adding to the buffer
            ndoc = normalize_document(doc)
            buffer.append(ndoc)
            processed_docs += 1

            # Collect some basic stats about stats_snapshots
            try:
                s = ndoc.get("stats_snapshots")
                if s and s != "[]":
                    arr = json.loads(s)
                    if isinstance(arr, list) and arr:
                        snap_nonempty += 1
                        if first_snap_example is None:
                            first_snap_example = arr[:2]
            except Exception:
                # Snapshot stats are best-effort only; ignore parsing issues.
                pass

            # Once we reach the chunk size, flush to Parquet.
            if len(buffer) >= args.chunk:
                df = pd.DataFrame(buffer)
                all_cols.update(df.columns)

                # Make sure every known column exists in the current DataFrame.
                # Missing columns are added as None to keep schema consistent.
                for c in all_cols:
                    if c not in df.columns:
                        df[c] = None

                # Normalize column order (sorted) to avoid random ordering
                # differences between chunks.
                df = df[list(sorted(all_cols))]
                table = pa.Table.from_pandas(df, preserve_index=False)

                # Initialize the Parquet writer on first chunk, recording schema.
                if writer is None:
                    writer = pq.ParquetWriter(
                        out_path,
                        table.schema,
                        compression=codec,
                        use_dictionary=True,
                        compression_level=(
                            args.compression_level
                            if codec in ("zstd", "brotli", "gzip")
                            else None
                        ),
                    )
                    writer_cols = list(writer.schema.names)

                # Align to writer's schema (column order + subset) in case
                # something changed between chunks.
                if writer_cols is not None:
                    table = table.select(writer_cols)

                writer.write_table(table)
                total_rows += len(buffer)
                print(f"[INFO] Wrote chunk — total rows: {total_rows:,}")
                buffer.clear()

        # Final flush for any remaining documents in the buffer.
        if buffer:
            df = pd.DataFrame(buffer)
            all_cols.update(df.columns)

            for c in all_cols:
                if c not in df.columns:
                    df[c] = None

            df = df[list(sorted(all_cols))]
            table = pa.Table.from_pandas(df, preserve_index=False)

            if writer is None:
                # In case the cursor was very small and we never initialized
                # the writer in the main loop.
                writer = pq.ParquetWriter(
                    out_path,
                    table.schema,
                    compression=codec,
                    use_dictionary=True,
                    compression_level=(
                        args.compression_level
                        if codec in ("zstd", "brotli", "gzip")
                        else None
                    ),
                )
                writer_cols = list(writer.schema.names)

            if writer_cols is not None:
                table = table.select(writer_cols)

            writer.write_table(table)
            total_rows += len(buffer)
            print(f"[INFO] Wrote final chunk — total rows: {total_rows:,}")

    finally:
        # Always close cursor and Parquet writer, even on exceptions.
        cursor.close()
        if writer:
            writer.close()

    # Summary stats for the run.
    print(f"[STATS] Documents processed: {processed_docs:,}")
    print(f"[STATS] Rows with non-empty stats_snapshots: {snap_nonempty:,} / {processed_docs:,}")
    if first_snap_example is not None:
        print(f"[STATS] Example snapshots: {first_snap_example}")

    print(f"[DONE] Export finished → {out_path}")


if __name__ == "__main__":
    main()
