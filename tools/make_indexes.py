#!/usr/bin/env python3
"""
make_indexes.py — Smart MongoDB Index Manager (v7, tuned for new pipeline)

- Dựa trên script v6 gốc của bạn, nhưng đã cập nhật:
    • Dùng h24 (ml_flags.viral_v2.h24.*) thay cho h24_validation.
    • Dùng tracking.next_poll_ts (không còn next_poll_after).
    • Bỏ toàn bộ indexes cho processed_videos / process_video theo yêu cầu.

- Vẫn giữ:
    • config.env loader (load_env / get_env)
    • Smart manager: --show-only, --drop-old, --rebuild, --collections
    • Indexes tối ưu cho:
        - tracking queue (track_once)
        - low_quality 3h & 6h workers
        - viral_v2 6h / 12h / 24h + final
        - video discover / region / query / channel
"""

from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Tuple

from pymongo import MongoClient
from pymongo.errors import OperationFailure

from config.env import load_env, get_env


# ---------------- Logging (console only) ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


# ---------------- CLI ----------------
def parse_args() -> argparse.Namespace:
    """
    Parse CLI arguments and resolve environment-driven defaults.
    """
    load_env()

    p = argparse.ArgumentParser(description="Smart MongoDB index manager (v7).")

    p.add_argument(
        "--mongo-uri",
        default=get_env("MONGO_URI", "mongodb://127.0.0.1:27017/ytscan"),
        help=(
            "MongoDB URI (default: from MONGO_URI or "
            "mongodb://127.0.0.1:27017/ytscan)"
        ),
    )
    p.add_argument(
        "--db",
        default=get_env("MONGO_DB"),
        help=(
            "DB name. Default: from MONGO_DB, or inferred from URI tail "
            "(e.g. mongodb://host:27017/ytscan → ytscan)."
        ),
    )
    p.add_argument(
        "--show-only",
        action="store_true",
        help="Show what would be done, but make no changes.",
    )
    p.add_argument(
        "--drop-old",
        action="store_true",
        help="Drop indexes that are not in INDEX_MAP (by key signature).",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Drop all non-_id_ indexes on selected collections, then recreate "
            "all indexes defined in INDEX_MAP."
        ),
    )
    p.add_argument(
        "--collections",
        type=str,
        default="all",
        help="Comma-separated list of collections (default: all from INDEX_MAP).",
    )

    return p.parse_args()


# ---------------- Mongo helpers ----------------
def _infer_db_name_from_uri(uri: str, fallback: str = "ytscan") -> str:
    tail = uri.rsplit("/", 1)[-1]
    db_part = tail.split("?", 1)[0]
    return db_part or fallback


def connect_db(mongo_uri: str, explicit_db: str | None):
    client = MongoClient(mongo_uri)
    if explicit_db:
        db = client[explicit_db]
    else:
        db_name = _infer_db_name_from_uri(mongo_uri, fallback="ytscan")
        db = client[db_name]
    return client, db


# ---------------- Index Map ----------------
# Cấu trúc:
#   INDEX_MAP: Dict[str, List[dict]]
#   mỗi dict:
#     {
#         "keys":   [("field", 1 or -1), ...],
#         "name":   "optional_name",
#         "unique": bool,
#         "partial": dict (partialFilterExpression),
#     }
INDEX_MAP: Dict[str, List[dict]] = {
    # === SOURCE COLLECTIONS ===
    "videos": [
        # ------------------------------------------------------------------
        # Tracking queues (track_once)
        # ------------------------------------------------------------------
        # Queue index:
        #   (tracking.status, tracking.next_poll_ts)
        #   partial trên các trạng thái active.
        {
            "keys": [
                ("tracking.status", 1),
                ("tracking.next_poll_ts", 1),
            ],
            "name": "trackStatus_nextPoll_active",
            "partial": {
                "tracking.status": {"$in": ["queued", "tracking", "retry"]},
            },
        },
        # Used for filtering by status và sort publish desc.
        {
            "keys": [
                ("tracking.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "trackStatus_publishedAt_desc",
        },
        # Channel-level queries: fetch videos for a channel ordered by publish time.
        {
            "keys": [
                ("snippet.channelId", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "channelId_publishedAt_desc",
        },
        # Region filter + publish date (discover / analytics).
        {
            "keys": [
                ("source.regionCode", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "region_publishedAt_desc",
        },
        # Query seed + publish date (discover by query).
        {
            "keys": [
                ("source.query", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "query_publishedAt_desc",
            "partial": {
                "source.query": {"$exists": True, "$type": "string"},
            },
        },
        # Category / length bucket filters.
        {
            "keys": [
                ("snippet.categoryId", 1),
                ("snippet.lengthBucket", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "category_lengthBucket_publishedAt_desc",
        },
        # Global recency index (most recent videos first).
        {
            "keys": [
                ("snippet.publishedAt", -1),
            ],
            "name": "publishedAt_desc",
        },

        # ------------------------------------------------------------------
        # Shared base for low-quality workers (3h & 6h)
        # ------------------------------------------------------------------
        {
            "keys": [
                ("stats_snapshots.0", 1),
            ],
            "name": "snap0_exists",
        },
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("tracking.status", 1),
            ],
            "name": "snap0_trackingStatus",
        },

        # ------------------------------------------------------------------
        # low_quality_v3_6h ML pipeline
        # ------------------------------------------------------------------
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v3_6h.updated_at", 1),
            ],
            "name": "lowq6h_snap0_updatedAt",
        },
        {
            "keys": [
                ("ml_flags.low_quality_v3_6h.is_low", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "lowq6h_isLow_publishedAt_desc",
        },
        {
            "keys": [
                ("tracking.status", 1),
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v3_6h.updated_at", 1),
            ],
            "name": "lowq6h_track_snap0_updatedAt_tracking",
            "partial": {
                "tracking.status": "tracking",
                "stats_snapshots.0": {"$exists": True},
            },
        },

        # ------------------------------------------------------------------
        # low_quality_v1_3h ML pipeline
        # ------------------------------------------------------------------
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v1_3h.updated_at", 1),
            ],
            "name": "lowq3h_snap0_updatedAt",
        },
        {
            "keys": [
                ("ml_flags.low_quality_v1_3h.is_low", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "lowq3h_isLow_publishedAt_desc",
        },
        {
            "keys": [
                ("tracking.status", 1),
                ("stats_snapshots.0", 1),
                ("ml_flags.low_quality_v1_3h.updated_at", 1),
            ],
            "name": "lowq3h_track_snap0_updatedAt_tracking",
            "partial": {
                "tracking.status": "tracking",
                "stats_snapshots.0": {"$exists": True},
            },
        },

        # ====================================================
        # Viral_v2 ML pipeline (6h / 12h / 24h + final)
        # ====================================================

        # 6h stage
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h6.score_proba", 1),
            ],
            "name": "viral_h6_snap0_score",
        },

        # 12h stage
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h12.score_proba", 1),
            ],
            "name": "viral_h12_snap0_score",
        },

        # 24h stage — schema mới dùng h24
        {
            "keys": [
                ("stats_snapshots.0", 1),
                ("ml_flags.viral_v2.h24.score_proba", 1),
            ],
            "name": "viral_h24_snap0_score",
        },

        # Dashboard / analytics: filter by final.status + sort by publish time.
        {
            "keys": [
                ("ml_flags.viral_v2.final.status", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalStatus_publishedAt_desc",
        },

        # 🔹 NEW: Viral final status + behavior + publish time
        # Dùng cho trang Viral Filter:
        #   - Filter theo final.status (non_viral / weak_viral / viral / super_viral / non_viral_lowq)
        #   - Optional filter theo final.behavior (no_signal / fast_growth / etc.)
        #   - Sort theo snippet.publishedAt desc
        {
            "keys": [
                ("ml_flags.viral_v2.final.status", 1),
                ("ml_flags.viral_v2.final.behavior", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalStatus_behavior_publishedAt_desc",
        },

        # 🔹 NEW: Viral final decided_stage + publish time
        # Hỗ trợ case phân tích video được quyết định ở stage nào
        # (low_quality / 6h / 12h / 24h) + sort theo publishAt.
        {
            "keys": [
                ("ml_flags.viral_v2.final.decided_stage", 1),
                ("snippet.publishedAt", -1),
            ],
            "name": "viral_finalDecidedStage_publishedAt_desc",
        },

        # Quick sort by latest_stats_ts (finalize / age filters).
        {
            "keys": [
                ("latest_stats_ts", -1),
            ],
            "name": "latestStatsTs_desc",
        },
    ],

    # === CHANNELS COLLECTION === (giữ, không đụng process_video nữa) ===
    "channels": [
        {
            "keys": [("handle", 1)],
            "name": "handle_uniq",
            "unique": True,
            "partial": {"handle": {"$exists": True, "$type": "string"}},
        },
        {
            "keys": [("last_updated", -1)],
            "name": "lastUpdated_desc",
        },
    ],
}


# ---------------- Index utilities ----------------
def _index_signature(ixdoc) -> Tuple[Tuple[str, int], ...]:
    return tuple(ixdoc["key"].items())


def _existing_indexes(coll):
    return list(coll.list_indexes())


def create_or_verify_collection_indexes(
    db,
    coll_name: str,
    specs: List[dict],
    show_only: bool = False,
) -> Tuple[int, int]:
    coll = db[coll_name]
    existing = _existing_indexes(coll)

    existing_by_keys = {_index_signature(ix): ix for ix in existing}
    existing_by_name = {ix["name"]: ix for ix in existing}

    logging.info(f"\n📂 Collection: {coll_name}")
    created = skipped = 0

    for spec in specs:
        keys = spec["keys"]
        name = spec.get("name")
        unique = spec.get("unique", False)
        partial = spec.get("partial")
        key_tuple = tuple(keys)

        # 1) Same keys already exist -> skip
        if key_tuple in existing_by_keys:
            ix = existing_by_keys[key_tuple]
            logging.info(
                f"   ⏭️  Skipped existing index with same keys: {keys} "
                f"(existing name={ix.get('name')})"
            )
            skipped += 1
            continue

        # 2) Same name but different keys -> warn & skip
        if name and name in existing_by_name:
            ix = existing_by_name[name]
            if _index_signature(ix) != key_tuple:
                logging.warning(
                    f"   ⚠️ Existing index with name '{name}' has different keys "
                    f"{list(ix['key'].items())}, skipping creation of {keys}."
                )
                skipped += 1
                continue

        if show_only:
            logging.info(
                f"   👀 Would create index: {keys}"
                + (f" [name={name}]" if name else "")
                + (" [unique]" if unique else "")
                + (f" [partial={partial}]" if partial else "")
            )
            continue

        opts = {"background": True}
        if name:
            opts["name"] = name
        if unique:
            opts["unique"] = True
        if partial:
            opts["partialFilterExpression"] = partial

        try:
            coll.create_index(keys, **opts)
            logging.info(
                f"   ✅ Created index: {keys}"
                + (f" [name={name}]" if name else "")
                + (" [unique]" if unique else "")
                + (f" [partial]" if partial else "")
            )
            created += 1
        except OperationFailure as e:
            if getattr(e, "code", None) == 85:  # IndexOptionsConflict
                logging.warning(
                    f"   ⚠️ Index conflict for {name or keys}: {e}. Skipping."
                )
                skipped += 1
            else:
                raise

    return created, skipped


def drop_unused_indexes(db, coll_name: str, keep_specs: List[dict]) -> None:
    coll = db[coll_name]
    existing = _existing_indexes(coll)
    keep_signatures = {tuple(s["keys"]) for s in keep_specs}

    for ix in existing:
        if ix["name"] == "_id_":
            continue
        if _index_signature(ix) not in keep_signatures:
            coll.drop_index(ix["name"])
            logging.info(f"   🗑️  Dropped old index: {ix['name']}")


def drop_all_indexes(db, coll_name: str, show_only: bool = False) -> None:
    coll = db[coll_name]
    existing = _existing_indexes(coll)

    for ix in existing:
        name = ix["name"]
        if name == "_id_":
            continue

        if show_only:
            logging.info(f"   👀 Would drop index: {name}")
        else:
            coll.drop_index(name)
            logging.info(f"   🗑️  Dropped index: {name}")


# ---------------- Main entrypoint ----------------
def main() -> int:
    args = parse_args()
    client, db = connect_db(args.mongo_uri, args.db)

    if args.rebuild and args.drop_old:
        logging.warning(
            "⚠️ Both --rebuild and --drop-old were specified. "
            "--rebuild takes precedence for dropping indexes."
        )

    collections = (
        list(INDEX_MAP.keys())
        if args.collections.lower() == "all"
        else [c.strip() for c in args.collections.split(",")]
    )

    total_created = total_skipped = 0
    logging.info("🚀 Starting MongoDB index maintenance...\n")
    logging.info(f"Using DB: {db.name} (uri={args.mongo_uri})")

    for coll_name in collections:
        if coll_name not in INDEX_MAP:
            logging.warning(f"⚠️ Unknown collection '{coll_name}' (skipped).")
            continue

        specs = INDEX_MAP[coll_name]

        if args.rebuild:
            drop_all_indexes(db, coll_name, show_only=args.show_only)

        created, skipped = create_or_verify_collection_indexes(
            db,
            coll_name,
            specs,
            show_only=args.show_only,
        )
        total_created += created
        total_skipped += skipped

        if args.drop_old and not args.rebuild and not args.show_only:
            drop_unused_indexes(db, coll_name, specs)

    logging.info("\n✅ Index maintenance complete.")
    logging.info(f"   Total created: {total_created}")
    logging.info(f"   Total skipped: {total_skipped}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
