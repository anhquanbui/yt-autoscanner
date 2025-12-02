#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ad_safety.py — Fetch YouTube text metadata for ad-safety training.

Design
------
- Use `_id` from `videos` as the primary key in `ad_safety`.
    => `ad_safety._id` = `videos._id` (1–1 mapping, both are YouTube videoId strings).

- For each video in `videos`:
    * Read `_id` (YouTube videoId).
    * Call YouTube Data API to fetch:
        - snippet.title
        - snippet.description
        - snippet.tags
        - snippet.defaultLanguage / defaultAudioLanguage
    * Build combined_text = "[TITLE] ...\\n[DESC] ...\\n[TAGS] ...".
    * Upsert into `ad_safety` collection.

- NO writes back to `videos`.

CLI options
-----------
--only-missing : only process videos that do NOT exist in `ad_safety`.
--limit N      : process at most N videos.
--batch-size N : Mongo batch size (reading IDs from `videos`).
--api-batch N  : YouTube API batch size (max 50 by API spec; default 50).

Env vars expected
-----------------
- Mongo:
    * via config.db.get_db() (MONGO_URI, MONGO_DB, ...).
- YouTube API key (one of):
    * YOUTUBE_API_KEY
    * YT_API_KEY
    * GOOGLE_API_KEY
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from pymongo import UpdateOne

from config.db import get_db
from config.env import get_env


YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"

# Defaults
MONGO_BATCH_DEFAULT = 1000
YT_API_BATCH_DEFAULT = 50  # YouTube max is 50 IDs per call


# ----------------------------------------------------------------------
# Helper: YouTube API client (simple, using requests)
# ----------------------------------------------------------------------
class YouTubeClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_snippets(
        self,
        video_ids: List[str],
        part: str = "snippet",
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch snippets for a list of YouTube video IDs.

        Returns
        -------
        dict: video_id -> full API item (at least 'snippet').
        """
        if not video_ids:
            return {}

        params = {
            "part": part,
            "id": ",".join(video_ids),
            "key": self.api_key,
            "maxResults": len(video_ids),
        }

        resp = requests.get(YOUTUBE_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        out: Dict[str, Dict[str, Any]] = {}
        for item in data.get("items", []):
            vid = item.get("id")
            if not vid:
                continue
            out[vid] = item
        return out


# ----------------------------------------------------------------------
# Helpers for building text & iterating over videos
# ----------------------------------------------------------------------
def build_combined_text(
    title: Optional[str],
    description: Optional[str],
    tags: Optional[List[str]],
) -> str:
    """
    Concatenate title / description / tags into a single text field.
    """
    parts: List[str] = []

    title = (title or "").strip()
    desc = (description or "").strip()
    tags_text = " ".join(tags or []).strip()

    if title:
        parts.append(f"[TITLE] {title}")
    if desc:
        parts.append(f"[DESC] {desc}")
    if tags_text:
        parts.append(f"[TAGS] {tags_text}")

    return "\n".join(parts)


def iter_video_docs(
    videos_coll,
    ad_safety_coll,
    only_missing: bool,
    limit: Optional[int],
    mongo_batch: int,
) -> Any:
    """
    Iterate over documents in `videos` with optional 'only_missing' and 'limit'.

    Each yielded doc will have at least:
        - _id  (YouTube videoId)
        - snippet (if present)
    """
    last_id = None
    yielded = 0

    # Chỉ cần có _id là đủ (ở schema hiện tại _id chính là videoId)
    base_query: Dict[str, Any] = {
        "_id": {"$exists": True, "$ne": None},
    }

    while True:
        query = base_query.copy()
        if last_id is not None:
            query["_id"] = {"$gt": last_id}

        cursor = (
            videos_coll.find(
                query,
                projection={
                    "_id": 1,
                    "snippet": 1,  # dùng làm fallback nếu cần
                },
            )
            .sort("_id", 1)
            .limit(mongo_batch)
        )

        docs = list(cursor)
        if not docs:
            break

        last_id = docs[-1]["_id"]

        if only_missing:
            ids = [d["_id"] for d in docs]
            if not ids:
                continue

            existing_ids = set(
                ad_safety_coll.distinct("_id", {"_id": {"$in": ids}})
            )

            docs = [d for d in docs if d["_id"] not in existing_ids]

        for doc in docs:
            if limit is not None and yielded >= limit:
                return
            yielded += 1
            yield doc


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch YouTube text metadata into `ad_safety` collection."
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only create documents for videos not yet in `ad_safety`.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of videos to process.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MONGO_BATCH_DEFAULT,
        help=f"Mongo batch size for reading from `videos` (default: {MONGO_BATCH_DEFAULT}).",
    )
    parser.add_argument(
        "--api-batch",
        type=int,
        default=YT_API_BATCH_DEFAULT,
        help="YouTube API batch size (<=50, default: 50).",
    )

    args = parser.parse_args(argv)

    # ------------------------------------------------------------------
    # 1) Resolve YouTube API key
    # ------------------------------------------------------------------
    api_key = (
        get_env("YOUTUBE_API_KEY")
        or get_env("YT_API_KEY")
        or get_env("GOOGLE_API_KEY")
    )
    if not api_key:
        print(
            "[ad_safety] ERROR: No YouTube API key found. "
            "Please set YOUTUBE_API_KEY / YT_API_KEY / GOOGLE_API_KEY in your .env"
        )
        return 1

    if args.api_batch > 50:
        print("[ad_safety] WARNING: --api-batch > 50, capping at 50 (YouTube limit).")
        args.api_batch = 50

    yt_client = YouTubeClient(api_key)

    # ------------------------------------------------------------------
    # 2) Connect to Mongo
    # ------------------------------------------------------------------
    db = get_db()
    videos = db.videos
    ad_safety = db.ad_safety

    print(f"[ad_safety] Connected to MongoDB, db = {db.name!r}")

    total_videos = videos.estimated_document_count()
    print(f"[ad_safety] Total documents in `videos`: {total_videos}")

    if args.limit is not None:
        print(f"[ad_safety] Limit: {args.limit} videos")
    if args.only_missing:
        print("[ad_safety] Mode: only-missing (skip existing in `ad_safety`)")

    # ------------------------------------------------------------------
    # 3) Iterate over videos and fetch from YouTube in batches
    # ------------------------------------------------------------------
    pending_batch: List[Dict[str, Any]] = []
    processed = 0
    scanned = 0

    def flush_batch(batch_docs: List[Dict[str, Any]]) -> int:
        """
        For a batch of docs from `videos`, call YouTube API and upsert into `ad_safety`.

        Returns number of docs upserted/updated.
        """
        if not batch_docs:
            return 0

        video_ids = [str(d["_id"]) for d in batch_docs]
        if not video_ids:
            return 0

        try:
            api_items = yt_client.fetch_snippets(video_ids, part="snippet")
        except Exception as e:
            print(f"[ad_safety] ERROR: YouTube API call failed: {e}")
            return 0

        ops: List[UpdateOne] = []
        now = datetime.now(timezone.utc)

        for doc in batch_docs:
            vid = str(doc["_id"])   # YouTube videoId string
            item = api_items.get(vid)

            snippet = (item or {}).get("snippet") or {}
            local_snippet = doc.get("snippet") or {}

            title = snippet.get("title") or local_snippet.get("title")
            description = snippet.get("description")
            tags = snippet.get("tags") or []

            lang = (
                snippet.get("defaultAudioLanguage")
                or snippet.get("defaultLanguage")
            )

            combined_text = build_combined_text(title, description, tags)

            update_doc: Dict[str, Any] = {
                "_id": vid,          # same as videos._id
                "video_id": vid,     # redundant but tiện cho join/lookup
                "title": title,
                "description": description,
                "tags": tags,
                "lang": lang,
                "combined_text": combined_text,
                "fetched_at": now,
                "updated_at": now,
            }

            ops.append(
                UpdateOne(
                    {"_id": vid},
                    {
                        "$set": update_doc,
                        "$setOnInsert": {
                            "label": None,
                            "label_source": None,
                            "label_ts": None,
                            "pred_label": None,
                            "pred_score": None,
                            "pred_model_ver": None,
                            "pred_ts": None,
                        },
                    },
                    upsert=True,
                )
            )

        if not ops:
            return 0

        result = ad_safety.bulk_write(ops, ordered=False)
        upserted = result.upserted_count + result.modified_count
        return upserted

    # Iterate over videos
    for doc in iter_video_docs(
        videos_coll=videos,
        ad_safety_coll=ad_safety,
        only_missing=args.only_missing,
        limit=args.limit,
        mongo_batch=args.batch_size,
    ):
        scanned += 1
        pending_batch.append(doc)

        if len(pending_batch) >= args.api_batch:
            up = flush_batch(pending_batch)
            processed += up
            print(
                f"[ad_safety] API batch done: +{up} upserted/updated "
                f"(total processed: {processed}, scanned: {scanned})",
                flush=True,
            )
            pending_batch.clear()

    # Flush remaining docs
    if pending_batch:
        up = flush_batch(pending_batch)
        processed += up
        print(
            f"[ad_safety] Final API batch: +{up} upserted/updated "
            f"(total processed: {processed}, scanned: {scanned})",
            flush=True,
        )

    print(
        f"[ad_safety] Finished. Scanned videos: {scanned}, "
        f"upserted/updated docs in `ad_safety`: {processed}"
    )
    print("[ad_safety] Done.")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
