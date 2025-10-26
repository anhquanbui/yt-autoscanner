#!/usr/bin/env python3
"""
tools/backfill_channels.py — One-shot channel handle & stats backfill (enhanced)
Now also backfills if `stats` are missing or incomplete.
"""

from __future__ import annotations

import os
import sys
import io
import argparse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Set, Optional

import requests
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

load_dotenv(override=False)

API_KEY   = os.getenv("YT_API_KEY")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan")
EXIT_QUOTA = 88
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def batched(seq, n):
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def to_int_or_none(v: Optional[str]) -> Optional[int]:
    try:
        if v is None or v == "hidden":
            return None
        return int(v)
    except Exception:
        return None


def fetch_channel_snippets_and_stats(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {channelId: {title, handle, stats{subscriberCount,videoCount,viewCount}}}"""
    if not ids:
        return {}
    params = {
        "key": API_KEY,
        "part": "snippet,statistics",
        "id": ",".join(ids[:50])
    }
    r = requests.get(CHANNELS_URL, params=params, timeout=30)
    r.raise_for_status()
    out: Dict[str, Dict[str, Any]] = {}
    for it in r.json().get("items", []):
        cid = it.get("id")
        if not cid:
            continue
        sn = it.get("snippet", {}) or {}
        st = it.get("statistics", {}) or {}

        handle = None
        custom_url = sn.get("customUrl")
        if custom_url:
            handle = custom_url if custom_url.startswith("@") else ("@" + custom_url.lstrip("/"))

        out[cid] = {
            "title": sn.get("title"),
            "handle": handle,
            "stats": {
                "subscriberCount": to_int_or_none(st.get("subscriberCount")),
                "videoCount": to_int_or_none(st.get("videoCount")),
                "viewCount": to_int_or_none(st.get("viewCount")),
            }
        }
    return out


def pick_missing_or_stale(db, stale_hours: int, limit: int) -> List[str]:
    vids = db.videos.find({}, {"snippet.channelId": 1}).limit(1_000_000)
    all_ids: Set[str] = set()
    for v in vids:
        cid = (v.get("snippet") or {}).get("channelId")
        if cid:
            all_ids.add(cid)

    if not all_ids:
        return []

    now = datetime.now(timezone.utc)
    stale_cut = now - timedelta(hours=stale_hours) if stale_hours > 0 else None

    existing = db.channels.find({"_id": {"$in": list(all_ids)}}, {"last_checked_at": 1, "stats": 1})
    present = {d["_id"]: d for d in existing}

    result: List[str] = []
    for cid in all_ids:
        doc = present.get(cid)
        is_missing = doc is None

        missing_stats = False
        if doc and not isinstance(doc.get("stats"), dict):
            missing_stats = True
        elif doc:
            st = doc.get("stats") or {}
            if not any(k in st for k in ("subscriberCount", "videoCount", "viewCount")):
                missing_stats = True

        is_stale_by_time = False
        if not is_missing and stale_hours > 0:
            try:
                last_iso = doc.get("last_checked_at")
                last_dt = datetime.fromisoformat(str(last_iso)) if last_iso else None
                if last_dt is None or last_dt.tzinfo is None:
                    last_dt = (last_dt or datetime.min).replace(tzinfo=timezone.utc)
                is_stale_by_time = last_dt < stale_cut
            except Exception:
                is_stale_by_time = True

        if is_missing or missing_stats or is_stale_by_time:
            result.append(cid)
            if limit and len(result) >= limit:
                break

    return result


def main() -> int:
    if not API_KEY:
        print("Missing YT_API_KEY", file=sys.stderr)
        return 2

    p = argparse.ArgumentParser(description="Backfill channels collection from videos (handles + stats).")
    p.add_argument("--stale-hours", type=int, default=0, help="Also refresh channels older than N hours (default: 0 = only missing).")
    p.add_argument("--limit", type=int, default=2000, help="Cap number of channels to process this run (default: 2000).")
    p.add_argument("--dry-run", action="store_true", help="Do not write to DB; just print.")
    args = p.parse_args()

    client = MongoClient(MONGO_URI)
    db = client.get_database()

    targets = pick_missing_or_stale(db, stale_hours=args.stale_hours, limit=args.limit)
    if not targets:
        print("Nothing to backfill (no missing/stale/incomplete channels).")
        return 0
    print(f"Backfilling channels: {len(targets)} (stale_hours={args.stale_hours}, limit={args.limit})")

    processed = 0
    ops: List[UpdateOne] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        for chunk in batched(targets, 50):
            data = fetch_channel_snippets_and_stats(chunk)
            for cid in chunk:
                info = data.get(cid) or {}
                doc = {
                    "handle": info.get("handle"),
                    "title": info.get("title"),
                    "stats": info.get("stats", {}),
                    "last_checked_at": now_iso,
                }
                if args.dry_run:
                    print(f" - {cid} | handle={doc.get('handle')} | title={doc.get('title')} | stats={doc.get('stats')}")
                else:
                    ops.append(UpdateOne({"_id": cid}, {"$set": doc}, upsert=True))
            processed += len(chunk)

        if ops and not args.dry_run:
            db.channels.bulk_write(ops, ordered=False)

        print(f"Done. Processed: {processed} channels.")
        return 0

    except requests.HTTPError as e:
        try:
            body = e.response.json()
        except Exception:
            body = {"error": str(e)}
        print("YouTube API error:", body, file=sys.stderr)
        return 1

    except Exception as e:
        print("Error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
