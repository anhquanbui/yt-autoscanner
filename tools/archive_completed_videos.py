#!/usr/bin/env python3
"""
Archive completed videos -> videos_cold (HOT → COLD) with **time-based partitioning**

Safe & idempotent:
- Copies docs with tracking.status == "complete" (optionally older than X hours)
- UPSERT into **partitioned cold collections** (month/year/flat/rollover)
- Then DELETE from hot `videos`
- Supports DRY-RUN, batching, field trimming to shrink cold storage

Env vars (defaults):
  MONGO_URI=mongodb://localhost:27017/ytscan
  ARCHIVE_SRC=videos
  ARCHIVE_DST=videos_cold
  ARCHIVE_PARTITION=month           # month|year|flat|rollover
  ARCHIVE_MAX_PER_COLL=5000000      # for rollover mode
  ARCHIVE_BATCH=2000
  ARCHIVE_MIN_AGE_HOURS=0           # only archive if last_polled_at <= now-AGE (0 = off)
  ARCHIVE_DRY_RUN=1                 # 1=dry-run, 0=apply
  ARCHIVE_TRIM_FIELDS=1             # 1=trim heavy fields when archiving
  ARCHIVE_LOG_SAMPLE=5

Indexes recommended on each cold partition:
  db.videos_cold_YYYY_MM.createIndex({"_id":1},{unique:true})
  db.videos_cold_YYYY_MM.createIndex({"snippet.publishedAt":1})
"""
from __future__ import annotations
import os, sys, io
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from collections import defaultdict

from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

# UTF-8 console safety
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv(override=False)

MONGO_URI   = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
SRC_COL     = os.getenv("ARCHIVE_SRC", "videos")
DST_COL     = os.getenv("ARCHIVE_DST", "videos_cold")
PARTITION   = os.getenv("ARCHIVE_PARTITION", "month").lower()   # month|year|flat|rollover
MAX_PER_COLL= int(os.getenv("ARCHIVE_MAX_PER_COLL", "5000000"))
BATCH       = max(1, int(os.getenv("ARCHIVE_BATCH", "2000")))
MIN_AGE_H   = int(os.getenv("ARCHIVE_MIN_AGE_HOURS", "0"))
DRY_RUN     = os.getenv("ARCHIVE_DRY_RUN", "1").lower() in ("1","true","yes")
TRIM_FIELDS = os.getenv("ARCHIVE_TRIM_FIELDS", "1").lower() in ("1","true","yes")
LOG_SAMPLE  = max(0, int(os.getenv("ARCHIVE_LOG_SAMPLE", "5")))

# Fields likely heavy but not always needed for cold analytics
HEAVY_PATHS = (
    "snippet.thumbnails",   # thumbnail map can be bulky
    "description",          # if you store long description at root (optional)
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_cutoff_iso() -> str | None:
    if MIN_AGE_H <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MIN_AGE_H)
    return cutoff.isoformat()


def ensure_partition_indexes(db, name: str):
    try:
        db[name].create_index([("_id", 1)], unique=True, name="id")
        db[name].create_index([("snippet.publishedAt", 1)], name="pub")
    except Exception:
        pass


def parse_iso_safe(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def dst_collection_for(doc: Dict[str, Any], db) -> str:
    """Return destination cold collection name according to partition policy."""
    if PARTITION == "flat":
        return DST_COL

    # Prefer snippet.publishedAt; fallback to tracking.discovered_at; fallback to now
    pub = (doc.get("snippet") or {}).get("publishedAt")
    if not pub:
        pub = (doc.get("tracking") or {}).get("discovered_at") or now_iso()
    ts = parse_iso_safe(pub)

    if PARTITION == "year":
        return f"{DST_COL}_{ts.year}"
    if PARTITION == "month":
        return f"{DST_COL}_{ts.year}_{ts.month:02d}"
    if PARTITION == "rollover":
        # Simple rollover buckets: videos_cold_p0, p1, ... (by count)
        i = 0
        existing = set(db.list_collection_names())
        while True:
            name = f"{DST_COL}_p{i}"
            if name not in existing:
                return name
            try:
                stats = db.command("collstats", name)
                count = int(stats.get("count", 0))
            except Exception:
                count = 0
            if count < MAX_PER_COLL:
                return name
            i += 1
    return DST_COL


def match_query() -> Dict[str, Any]:
    q: Dict[str, Any] = {"tracking.status": "complete"}
    ac = age_cutoff_iso()
    if ac:
        q["tracking.last_polled_at"] = {"$lte": ac}
    return q


def trim_for_cold(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not TRIM_FIELDS:
        return doc
    d = dict(doc)
    sn = d.get("snippet")
    if isinstance(sn, dict):
        sn = dict(sn)
        sn.pop("thumbnails", None)
        d["snippet"] = sn
    d.pop("description", None)
    return d


def process_batch(db, docs: List[Dict[str, Any]], dry_run: bool) -> tuple[int, int]:
    if not docs:
        return 0, 0

    # Group docs by destination partition
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for d in docs:
        name = dst_collection_for(d, db)
        groups[name].append(d)

    moved = 0
    deleted = 0

    if dry_run:
        for name, grp in groups.items():
            ids = [g["_id"] for g in grp]
            print(f"[dry-run] would archive {len(grp)} docs -> {name}; sample={ids[:LOG_SAMPLE]}")
        return 0, 0

    # Upsert per partition, then delete from hot
    for name, grp in groups.items():
        ensure_partition_indexes(db, name)
        ops: List[UpdateOne] = []
        for g in grp:
            cold_doc = trim_for_cold(g)
            ops.append(UpdateOne({"_id": cold_doc["_id"]}, {"$set": cold_doc}, upsert=True))
        if ops:
            db[name].bulk_write(ops, ordered=False)
        moved += len(grp)

    # Delete from hot after all upserts
    ids = [d["_id"] for d in docs]
    res = db[SRC_COL].delete_many({"_id": {"$in": ids}})
    deleted += int(res.deleted_count or 0)

    return moved, deleted


def main() -> int:
    print(f">>> archive_completed_videos starting | src={SRC_COL} | partition={PARTITION} | batch={BATCH} | dry_run={DRY_RUN}")
    client = MongoClient(MONGO_URI)
    db = client.get_database()

    q = match_query()
    total = db[SRC_COL].count_documents(q)
    print(f"[match] total={total} | min_age_h={MIN_AGE_H}")

    moved = 0
    deleted = 0

    cur = db[SRC_COL].find(q, projection=None).batch_size(BATCH)
    batch: List[Dict[str, Any]] = []
    for doc in cur:
        batch.append(doc)
        if len(batch) >= BATCH:
            m, d = process_batch(db, batch, DRY_RUN)
            moved += m; deleted += d
            batch.clear()

    if batch:
        m, d = process_batch(db, batch, DRY_RUN)
        moved += m; deleted += d

    print(f"DONE. moved={moved}, deleted={deleted}, matched={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
