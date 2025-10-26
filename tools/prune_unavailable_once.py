#!/usr/bin/env python3
"""
Hard-delete cleaner for videos that should be removed fast.

Modes:
- PASSIVE: delete docs already marked by trackers as unavailable/no_publishedAt
- ACTIVE : (optional) verify a batch against YouTube API; delete if no stats

Safe features:
- DRY-RUN
- Batched deletes
- Simple metrics

Env vars (sane defaults):
  MONGO_URI=mongodb://localhost:27017/ytscan
  PRUNE_SOURCE=videos
  PRUNE_BATCH=2000
  PRUNE_DRY_RUN=1                 # 1=dry-run, 0=apply
  PRUNE_MIN_AGE_MIN=0             # only delete if last_polled_at <= now-AGE
  PRUNE_ACTIVE_VERIFY=0           # 1=enable active re-check via API
  YT_API_KEY=...                  # required if ACTIVE_VERIFY=1
  PRUNE_LOG_SAMPLE=5
"""
from __future__ import annotations
import os, sys, io
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta

import requests
from pymongo import MongoClient
from dotenv import load_dotenv

# UTF-8 console
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv(override=False)

MONGO_URI   = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
SRC_COL     = os.getenv("PRUNE_SOURCE", "videos")
BATCH       = max(1, int(os.getenv("PRUNE_BATCH", "2000")))
DRY_RUN     = os.getenv("PRUNE_DRY_RUN", "1").lower() in ("1","true","yes")
MIN_AGE_MIN = int(os.getenv("PRUNE_MIN_AGE_MIN", "0"))
ACTIVE_VFY  = os.getenv("PRUNE_ACTIVE_VERIFY", "0").lower() in ("1","true","yes")
LOG_SAMPLE  = max(0, int(os.getenv("PRUNE_LOG_SAMPLE", "5")))

API_KEY     = os.getenv("YT_API_KEY")
VIDEOS_URL  = "https://www.googleapis.com/youtube/v3/videos"

STOP_SET = {"unavailable", "no_publishedAt"}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_cutoff_iso() -> str | None:
    if MIN_AGE_MIN <= 0:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_AGE_MIN)
    return cutoff.isoformat()


def fetch_stats(ids: List[str]) -> set[str]:
    """Return set of IDs that DO have statistics."""
    if not ids:
        return set()
    params = {"key": API_KEY, "part": "statistics", "id": ",".join(ids[:50])}
    r = requests.get(VIDEOS_URL, params=params, timeout=30)
    r.raise_for_status()
    found: set[str] = set()
    for it in r.json().get("items", []):
        vid = it.get("id")
        if vid and it.get("statistics") is not None:
            found.add(vid)
    return found


def passive_candidates(db) -> List[str]:
    q: Dict[str, Any] = {"tracking.stop_reason": {"$in": list(STOP_SET)}}
    ac = age_cutoff_iso()
    if ac:
        q["tracking.last_polled_at"] = {"$lte": ac}
    cur = db[SRC_COL].find(q, {"_id": 1}).batch_size(BATCH)
    return [str(d["_id"]) for d in cur]


def active_candidates(db) -> List[str]:
    """Optionally re-check a pool of likely-problem docs.
    Strategy: no publishedAt OR status=tracking but missing snippet.publishedAt.
    """
    q: Dict[str, Any] = {
        "$or": [
            {"snippet.publishedAt": {"$exists": False}},
            {"snippet.publishedAt": None},
        ]
    }
    ac = age_cutoff_iso()
    if ac:
        q["tracking.last_polled_at"] = {"$lte": ac}
    cur = db[SRC_COL].find(q, {"_id": 1}).limit(BATCH)
    return [str(d["_id"]) for d in cur]


def delete_ids(db, ids: List[str]) -> int:
    if not ids:
        return 0
    if DRY_RUN:
        print(f"[dry-run] would delete {len(ids)} videos; sample={ids[:LOG_SAMPLE]}")
        return 0
    res = db[SRC_COL].delete_many({"_id": {"$in": ids}})
    print(f"[delete] deleted={res.deleted_count} (requested={len(ids)})")
    return int(res.deleted_count or 0)


def main() -> int:
    print(f">>> prune_unavailable_once starting | src={SRC_COL} | dry_run={DRY_RUN} | active_verify={ACTIVE_VFY}")
    client = MongoClient(MONGO_URI)
    db = client.get_database()

    # PASSIVE phase: delete what tracker already marked
    p_ids = passive_candidates(db)
    print(f"[passive] candidates={len(p_ids)} (stop_reason in {STOP_SET})")
    deleted = 0
    for i in range(0, len(p_ids), BATCH):
        deleted += delete_ids(db, p_ids[i:i+BATCH])

    # ACTIVE phase: optionally verify missing-publishedAt items with API
    if ACTIVE_VFY:
        if not API_KEY:
            print("ACTIVE_VERIFY requested but YT_API_KEY missing; skipping.", file=sys.stderr)
        else:
            a_ids = active_candidates(db)
            print(f"[active] verify pool={len(a_ids)}")
            to_del: List[str] = []
            for i in range(0, len(a_ids), 50):
                batch = a_ids[i:i+50]
                try:
                    found = fetch_stats(batch)  # IDs that do have stats
                except Exception as e:
                    print("[active] API error:", e, file=sys.stderr)
                    continue
                for vid in batch:
                    if vid not in found:
                        to_del.append(vid)
            print(f"[active] verified to delete={len(to_del)}")
            for i in range(0, len(to_del), BATCH):
                deleted += delete_ids(db, to_del[i:i+BATCH])

    print(f"DONE. total_deleted={deleted}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
