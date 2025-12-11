"""
Keyword stats worker for yt-autoscanner (all_time).

Đọc từ collection `videos` và build collection `keyword_stats`
theo schema (keyword, region) + region="ALL" cho tổng toàn region.

Dùng config.env + config.db.get_db() để kết nối Mongo đúng MONGO_URI/MONGO_DB
đã set trong .env.
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, List, Dict, Any, Optional

from pymongo import ASCENDING
from config.db import get_db  # <-- dùng helper chung

# ---------------------------------------------------------------------------
# Collections / worker name
# ---------------------------------------------------------------------------

VIDEOS_COLLECTION = "videos"
KW_STATS_COLLECTION = "keyword_stats"
WORKER_RUNS_COLLECTION = "worker_runs"

WORKER_NAME = "keyword_stats_all_time"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)

# ---------------------------------------------------------------------------
# Helpers for reading fields from video docs
# ---------------------------------------------------------------------------


def normalize_keyword(kw: str) -> str:
    """Normalize keyword before using in stats."""
    return (kw or "").strip().lower()


def parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string to datetime with timezone if present."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None


def get_24h_snapshot(
    published_at: Optional[datetime],
    snapshots: List[Dict[str, Any]],
) -> Dict[str, int]:
    """
    From stats_snapshots, approximate 24h metrics.

    - Nếu có published_at:
        - cutoff = published_at + 24h
        - chọn snapshot có ts <= cutoff và gần cutoff nhất
        - nếu không có, fallback snapshot cuối cùng.
    - Nếu không có published_at:
        - dùng snapshot cuối cùng.

    Return:
        { views_24h, likes_24h, comments_24h }
    """
    if not snapshots:
        return {"views_24h": 0, "likes_24h": 0, "comments_24h": 0}

    parsed = []
    for snap in snapshots:
        ts_raw = snap.get("ts")
        ts = parse_iso_dt(ts_raw) if isinstance(ts_raw, str) else None
        if ts is None:
            continue
        parsed.append((ts, snap))

    if not parsed:
        last = snapshots[-1]
        return {
            "views_24h": int(last.get("viewCount") or 0),
            "likes_24h": int(last.get("likeCount") or 0),
            "comments_24h": int(last.get("commentCount") or 0),
        }

    parsed.sort(key=lambda x: x[0])
    last_dt, last_snap = parsed[-1]

    if not published_at:
        return {
            "views_24h": int(last_snap.get("viewCount") or 0),
            "likes_24h": int(last_snap.get("likeCount") or 0),
            "comments_24h": int(last_snap.get("commentCount") or 0),
        }

    cutoff = published_at + timedelta(hours=24)
    candidates = [p for p in parsed if p[0] <= cutoff]

    if candidates:
        _, snap_24h = candidates[-1]
        snap = snap_24h
    else:
        snap = last_snap

    return {
        "views_24h": int(snap.get("viewCount") or 0),
        "likes_24h": int(snap.get("likeCount") or 0),
        "comments_24h": int(snap.get("commentCount") or 0),
    }


def extract_metrics_from_video(doc: Dict[str, Any]) -> Tuple[int, int, int]:
    """Extract approx 24h views/likes/comments using snippet.publishedAt + stats_snapshots."""
    snippet = doc.get("snippet") or {}
    published_at_str = snippet.get("publishedAt")
    published_at = parse_iso_dt(published_at_str)

    snapshots = doc.get("stats_snapshots") or []
    metrics = get_24h_snapshot(published_at, snapshots)

    return (
        metrics["views_24h"],
        metrics["likes_24h"],
        metrics["comments_24h"],
    )


def extract_label_from_video(doc: Dict[str, Any]) -> str:
    """
    Extract final viral label từ ml_flags.viral_v2.final.status.

    - "weak_viral"   -> "weak_viral"
    - "viral"        -> "viral"
    - "super_viral"  -> "super_viral"
    - "non_viral" / "non_viral_lowq" / ... -> "non_viral"
    """
    ml_flags = doc.get("ml_flags") or {}
    viral_v2 = ml_flags.get("viral_v2") or {}
    final = viral_v2.get("final") or {}
    status = (final.get("status") or "").lower()

    if status.startswith("non_viral"):
        return "non_viral"
    if status in {"weak_viral", "viral", "super_viral"}:
        return status
    return "non_viral"


def extract_region_from_video(doc: Dict[str, Any]) -> str:
    """Extract region code from source.regionCode, default 'UNKNOWN'."""
    source = doc.get("source") or {}
    region = source.get("regionCode") or "UNKNOWN"
    return str(region).upper()


def extract_keywords_from_video(doc: Dict[str, Any]) -> List[str]:
    """
    Extract keyword list từ video doc.

    Hiện tại dùng source.query làm keyword chính.
    Sau này nếu có thêm mảng keywords thì có thể mở rộng.
    """
    source = doc.get("source") or {}
    query_kw = source.get("query")
    if not query_kw:
        return []
    kw = normalize_keyword(str(query_kw))
    return [kw] if kw else []


def extract_ad_safe_flag(doc: Dict[str, Any]) -> bool:
    """
    Extract ad-safe flag từ ml_flags.ad_friendly_v1.label.

    - "AD_FRIENDLY" -> True
    - cái khác      -> False (tạm thời, có thể tinh chỉnh sau)
    """
    ml_flags = doc.get("ml_flags") or {}
    ad_flag = ml_flags.get("ad_friendly_v1") or {}
    label = ad_flag.get("label")

    if isinstance(label, str):
        if label.upper() == "AD_FRIENDLY":
            return True
        return False

    return False


def label_to_inc_fields(label: str) -> Dict[str, int]:
    """Map viral label → weak_viral_count / viral_count / super_viral_count."""
    label = (label or "").lower()
    return {
        "weak_viral_count": 1 if label == "weak_viral" else 0,
        "viral_count": 1 if label == "viral" else 0,
        "super_viral_count": 1 if label == "super_viral" else 0,
    }


# ---------------------------------------------------------------------------
# Keyword stats update logic
# ---------------------------------------------------------------------------


def update_keyword_stats_for_region(
    db,
    keyword: str,
    region: str,
    views_24h: int,
    likes_24h: int,
    comments_24h: int,
    label: str,
    is_ad_safe: bool,
    dry_run: bool = False,
):
    """
    Update keyword_stats cho (keyword, region) và (keyword, 'ALL').
    """
    now = datetime.now(timezone.utc)

    base_inc = {
        "videos_count": 1,
        "views_sum_24h": views_24h,
        "likes_sum_24h": likes_24h,
        "comments_sum_24h": comments_24h,
        "ad_safe_count": 1 if is_ad_safe else 0,
        "ad_risky_count": 0 if is_ad_safe else 1,
    }
    base_inc.update(label_to_inc_fields(label))

    min_doc = {
        "views_min_24h": views_24h,
        "likes_min_24h": likes_24h,
        "comments_min_24h": comments_24h,
    }

    max_doc = {
        "views_max_24h": views_24h,
        "likes_max_24h": likes_24h,
        "comments_max_24h": comments_24h,
    }

    def _update(region_value: str):
        if dry_run:
            logger.debug("[DRY RUN] Would update (%s, %s)", keyword, region_value)
            return

        db[KW_STATS_COLLECTION].update_one(
            {"keyword": keyword, "region": region_value},
            {
                "$inc": base_inc,
                "$min": min_doc,
                "$max": max_doc,
                "$setOnInsert": {"first_seen_at": now},
                "$set": {"last_updated": now},
            },
            upsert=True,
        )

    _update(region)
    _update("ALL")


def process_video_document(db, doc: Dict[str, Any], dry_run: bool = False) -> int:
    """
    Xử lý 1 video:
    - lấy keyword từ source.query
    - lấy region từ source.regionCode
    - tính ~24h metrics từ stats_snapshots
    - suy ra viral label + ad_safe
    - update keyword_stats
    - set kw_stats_processed = True

    Return: số lần update keyword (số keyword).
    """
    video_id = doc.get("_id")
    keywords = extract_keywords_from_video(doc)

    if not keywords:
        if not dry_run and video_id is not None:
            db[VIDEOS_COLLECTION].update_one(
                {"_id": video_id}, {"$set": {"kw_stats_processed": True}}
            )
        return 0

    region = extract_region_from_video(doc)
    label = extract_label_from_video(doc)
    is_ad_safe = extract_ad_safe_flag(doc)
    views_24h, likes_24h, comments_24h = extract_metrics_from_video(doc)

    kw_updates = 0
    for kw in keywords:
        update_keyword_stats_for_region(
            db=db,
            keyword=kw,
            region=region,
            views_24h=views_24h,
            likes_24h=likes_24h,
            comments_24h=comments_24h,
            label=label,
            is_ad_safe=is_ad_safe,
            dry_run=dry_run,
        )
        kw_updates += 1

    if not dry_run and video_id is not None:
        db[VIDEOS_COLLECTION].update_one(
            {"_id": video_id}, {"$set": {"kw_stats_processed": True}}
        )

    return kw_updates


# ---------------------------------------------------------------------------
# Worker modes
# ---------------------------------------------------------------------------


def run_incremental(db, limit: int, dry_run: bool = False) -> Tuple[int, int]:
    """
    Incrementally process videos that have not yet been aggregated into keyword_stats.

    Selection rule:
    - kw_stats_processed != True
    - ml_flags.viral_v2.final.status exists
    """
    started_at = datetime.now(timezone.utc)
    logger.info(
        "Starting incremental run (limit=%d, dry_run=%s)",
        limit,
        dry_run,
    )

    query = {
        "kw_stats_processed": {"$ne": True},
        "ml_flags.viral_v2.final.status": {"$exists": True},
    }

    # 👇 Đếm trước tổng số video sẽ xử lý (bị giới hạn bởi limit)
    total_videos_to_process = db[VIDEOS_COLLECTION].count_documents(query)
    if total_videos_to_process > limit:
        total_videos_to_process = limit

    # Vì hiện tại mỗi video chỉ có 1 keyword (source.query),
    # nên số KW ước lượng ≈ số video
    logger.info(
        "Incremental run will process up to %d videos (~%d keywords)",
        total_videos_to_process,
        total_videos_to_process,
    )

    cursor = db[VIDEOS_COLLECTION].find(query, no_cursor_timeout=True).limit(limit)

    videos_processed = 0
    keyword_updates = 0

    try:
        for doc in cursor:
            videos_processed += 1
            kw_updates = process_video_document(db, doc, dry_run=dry_run)
            keyword_updates += kw_updates

            # 👇 Log tiến độ mỗi 100 video (hoặc tuỳ bạn chỉnh)
            if videos_processed % 100 == 0 or videos_processed == total_videos_to_process:
                pct = (
                    (videos_processed / total_videos_to_process) * 100
                    if total_videos_to_process > 0
                    else 0
                )
                logger.info(
                    "Progress: %d/%d videos (%.1f%%), %d keyword updates so far",
                    videos_processed,
                    total_videos_to_process,
                    pct,
                    keyword_updates,
                )
    finally:
        cursor.close()

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds()

    logger.info(
        "Incremental finished: videos=%d, kw_updates=%d, duration=%.2fs",
        videos_processed,
        keyword_updates,
        duration,
    )

    if not dry_run:
        db[WORKER_RUNS_COLLECTION].update_one(
            {"name": WORKER_NAME, "mode": "incremental"},  # 1 doc / worker+mode
            {
                "$set": {
                    "name": WORKER_NAME,
                    "worker_name": WORKER_NAME,
                    "mode": "incremental",

                    # dùng cho dashboard
                    "last_run": finished_at,
                    "last_started_at": started_at,
                    "last_finished_at": finished_at,

                    "status": "success",
                    "duration_sec": duration,
                    "videos_processed": videos_processed,
                    "keywords_updated": keyword_updates,
                },
                "$setOnInsert": {
                    "created_at": started_at,
                },
            },
            upsert=True,
        )

    return videos_processed, keyword_updates


def run_backfill(db, limit: int, dry_run: bool = False) -> Tuple[int, int]:
    """
    Backfill all_time:
    - Drop keyword_stats
    - Bỏ qua kw_stats_processed flag
    - Chạy lại từ đầu (limit chỉ giới hạn mỗi lần run).
    """
    started_at = datetime.now(timezone.utc)
    logger.info(
        "Starting BACKFILL run (limit=%d, dry_run=%s)",
        limit,
        dry_run,
    )

    if not dry_run:
        logger.warning("Dropping keyword_stats collection for full rebuild.")
        db[KW_STATS_COLLECTION].drop()

    query = {
        "ml_flags.viral_v2.final.status": {"$exists": True},
    }

    # 👇 Đếm tổng video có thể backfill (bị giới hạn bởi limit)
    total_videos_to_process = db[VIDEOS_COLLECTION].count_documents(query)
    if total_videos_to_process > limit:
        total_videos_to_process = limit

    logger.info(
        "Backfill will process up to %d videos (~%d keywords)",
        total_videos_to_process,
        total_videos_to_process,
    )

    cursor = db[VIDEOS_COLLECTION].find(query, no_cursor_timeout=True).limit(limit)

    videos_processed = 0
    keyword_updates = 0

    try:
        for doc in cursor:
            videos_processed += 1
            kw_updates = process_video_document(db, doc, dry_run=dry_run)
            keyword_updates += kw_updates

            if videos_processed % 100 == 0 or videos_processed == total_videos_to_process:
                pct = (
                    (videos_processed / total_videos_to_process) * 100
                    if total_videos_to_process > 0
                    else 0
                )
                logger.info(
                    "Progress (backfill): %d/%d videos (%.1f%%), %d keyword updates so far",
                    videos_processed,
                    total_videos_to_process,
                    pct,
                    keyword_updates,
                )
    finally:
        cursor.close()

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds()

    logger.info(
        "BACKFILL finished: videos=%d, kw_updates=%d, duration=%.2fs",
        videos_processed,
        keyword_updates,
        duration,
    )

    if not dry_run:
        # snapshot 1 doc duy nhất cho (worker_name, mode="backfill")
        db[WORKER_RUNS_COLLECTION].update_one(
            {
                "name": WORKER_NAME,
                "mode": "backfill"
            },
            {
                "$set": {
                    "name": WORKER_NAME,
                    "mode": "backfill",

                    "last_run": finished_at,
                    "last_started_at": started_at,
                    "last_finished_at": finished_at,

                    "status": "success",
                    "duration_sec": duration,
                    "videos_processed": videos_processed,
                    "keywords_updated": keyword_updates,
                },
                "$setOnInsert": {
                    "created_at": started_at,
                }
            },
            upsert=True
        )


    return videos_processed, keyword_updates


# ---------------------------------------------------------------------------
# CLI + indexes
# ---------------------------------------------------------------------------


def ensure_indexes(db):
    """Tạo index cơ bản cho keyword_stats và cờ kw_stats_processed."""
    logger.info("Ensuring indexes...")

    db[KW_STATS_COLLECTION].create_index(
        [("keyword", ASCENDING), ("region", ASCENDING)],
        name="keyword_region",
        unique=True,
    )
    db[VIDEOS_COLLECTION].create_index(
        "kw_stats_processed",
        name="kw_stats_processed_flag",
    )

    logger.info("Indexes ensured.")


def main():
    parser = argparse.ArgumentParser(description="Keyword stats worker (all_time).")
    parser.add_argument(
        "--mode",
        choices=["incremental", "backfill"],
        default="incremental",
        help="Run mode: incremental or backfill (default: incremental).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max number of videos to process in this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="If set, do not write any changes to MongoDB.",
    )

    args = parser.parse_args()

    # 🔥 Lấy DB qua config.db.get_db() – tự load .env + MONGO_URI
    db = get_db()

    ensure_indexes(db)

    if args.mode == "incremental":
        run_incremental(db, limit=args.limit, dry_run=args.dry_run)
    else:
        run_backfill(db, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
