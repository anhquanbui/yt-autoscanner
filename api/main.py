from pathlib import Path
from typing import Dict, Any, List

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from datetime import datetime, timezone

from config.db import get_db

# -----------------------------
# Paths
# -----------------------------
BASE_DIR = Path(__file__).resolve().parents[1]  # -> yt-autoscanner/
EXTERNAL_DIR = BASE_DIR / "external"

app = FastAPI(title="YouTube AutoScanner Dashboard API")

static_dir = EXTERNAL_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(EXTERNAL_DIR / "templates"))

# ============================================================
# 0. Shared helpers
# ============================================================


def load_dashboard_kpis() -> Dict[str, Any]:
    """
    Load latest snapshot from dashboard_kpis collection
    and compute derived KPIs for the overview page.
    """
    db = get_db()
    doc = db.dashboard_kpis.find_one(sort=[("ts", -1)]) or {}

    total_videos = int(doc.get("total_videos", 0))
    total_channels = int(doc.get("total_channels", 0))

    tracking_active = int(doc.get("tracking_active", 0))
    removed_unavailable = int(doc.get("completed_removed", 0))
    stopped_low_quality = int(doc.get("stopped_low_quality", 0))

    ad_friendly = int(doc.get("ad_friendly_total", 0))
    non_ad_friendly = int(doc.get("non_ad_friendly_total", 0))

    viral_weak = int(doc.get("viral2_final_weak_viral", 0))
    viral_main = int(doc.get("viral2_final_viral", 0))
    viral_super = int(doc.get("viral2_final_super_viral", 0))

    non_viral_main = int(doc.get("viral2_final_nonviral", 0))
    non_viral_lowq = int(doc.get("viral2_final_nonviral_lowq", 0))

    viral_unknown = int(doc.get("viral2_final_unknown", 0))

    viral_total = viral_weak + viral_main + viral_super
    non_viral = non_viral_main + non_viral_lowq

    def pct(x: int) -> float:
        return (x / total_videos * 100.0) if total_videos else 0.0

    return {
        "snapshot_ts": str(doc.get("ts", "")),
        "total_videos": total_videos,
        "total_channels": total_channels,
        # Tracking
        "tracking_active": tracking_active,
        "tracking_active_pct": pct(tracking_active),
        "removed_unavailable": removed_unavailable,
        "removed_unavailable_pct": pct(removed_unavailable),
        "stopped_low_quality": stopped_low_quality,
        "stopped_low_quality_pct": pct(stopped_low_quality),
        # Brand safety
        "ad_friendly": ad_friendly,
        "ad_friendly_pct": pct(ad_friendly),
        "non_ad_friendly": non_ad_friendly,
        "non_ad_friendly_pct": pct(non_ad_friendly),
        # Viral summary
        "viral_total": viral_total,
        "viral_total_pct": pct(viral_total),
        "non_viral": non_viral,
        "non_viral_pct": pct(non_viral),
        "viral_unknown": viral_unknown,
        "viral_unknown_pct": pct(viral_unknown),
        # Breakdown
        "viral_weak": viral_weak,
        "viral_main": viral_main,
        "viral_super": viral_super,
    }


# -----------------------------
# Viral Filter helpers
# -----------------------------

STATUS_LABELS = {
    # Viral strength (for both list + video analytics)
    "weak_viral": "🌡 Early Growth",
    "viral": "🔥 Going Viral",
    "super_viral": "🚀 Explosive Growth",
    "non_viral": "🌙 Low Growth",
    "non_viral_lowq": "🌙 Low Growth (low-quality filtered)",
    "viral_after_removed": "🟣 Viral (after removal)",
    "removed": "⚫ Removed / Unavailable",
    "unknown": "⚪ No decision yet",
    None: "⚪ No decision yet",
}

BEHAVIOR_LABELS = {
    "no_signal": "⚪ No signal",
    "early_peak": "🕒 Early peak",
    "late_growth": "🌱 Late growth",
    "consistent": "📈 Stable retention",
    "volatile": "🌪️ Spike-driven engagement",
    "neutral": "〰️ Neutral / unclear pattern",
    None: "⚪ Unknown behavior",
}

AD_LABELS = {
    "AD_FRIENDLY": "🟢 Suitable",
    "LIMITED": "🟡 Limited",
    "NOT_AD_FRIENDLY": "🔴 Not eligible",
    None: "Unknown",
}

TRACKING_STATUS_LABELS = {
    "complete": "⏹ Tracking complete",
    "running": "🔄 Tracking in progress",
    "queued": "⏳ Waiting to start",
    "stopped_manual": "🛑 Manually stopped",
    "unknown": "❔ Unknown",
    None: "❔ Unknown",
}

TRACKING_REASON_LABELS = {
    "super_viral_confirmed": "🚀 Stopped after confirming viral performance",
    "low_quality_filtered": "⚠ Stopped due to low-quality filter",
    "removed": "⚫ Stopped — video removed/unavailable",
    "timeout": "⏱ Stopped after tracking window",
    "manual_stop": "🛑 Manually stopped",
}

VIRAL_OPTIONS: List[str] = [
    "(All)",
    "🚀 Explosive Growth (super viral)",
    "🔥 Going Viral (viral)",
    "🌱 Early Growth (weak viral)",
    "🧊 No Viral Signal",
]

BEHAVIOR_OPTIONS: List[str] = [
    "(All)",
    "No signal",
    "Early peak",
    "Late growth",
    "Consistent",
    "Volatile",
    "Neutral / unclear",
]

# -----------------------------
# Keyword stats / analysis helpers
# -----------------------------

def _keyword_stats_coll():
    db = get_db()
    return db["keyword_stats"]


def load_keyword_regions_for_stats() -> List[str]:
    """
    Lấy danh sách region từ keyword_stats (ví dụ: ALL, US, IE, JP...).
    Đảm bảo 'ALL' nằm đầu danh sách nếu có.
    """
    coll = _keyword_stats_coll()
    regions = coll.distinct("region")
    regions = [r for r in regions if r]  # remove None / empty

    # Đưa ALL lên đầu nếu có
    regions_sorted = sorted([r for r in regions if r != "ALL"])
    if "ALL" in regions:
        return ["ALL"] + regions_sorted
    return regions_sorted


def load_keyword_stats_view(
    region: str,
    min_videos: int,
    limit: int,
    sort_by: str,
) -> Dict[str, Any]:
    """
    Đọc keyword_stats và chuẩn hóa data cho trang Keywords Analysis.

    - region:
        - "ALL"  -> chỉ lấy các doc region == "ALL" (tổng hợp tất cả region)
        - khác   -> chỉ lấy region đó
    - min_videos: filter theo videos_count >= min_videos
    - limit: số keyword tối đa hiển thị
    - sort_by: tiêu chí sort ("viral_ratio", "avg_views", "videos", "super_viral_ratio", "ad_safe_ratio")
    """
    coll = _keyword_stats_coll()

    filters: Dict[str, Any] = {}
    if region:
        filters["region"] = region

    if min_videos > 0:
        filters["videos_count"] = {"$gte": min_videos}

    # Lấy tối đa 5000 doc rồi sort trong Python cho linh hoạt
    max_docs = max(limit * 5, limit)
    cursor = coll.find(filters).limit(max_docs)

    rows_raw: List[Dict[str, Any]] = list(cursor)

    prepared_rows: List[Dict[str, Any]] = []

    total_videos_sum = 0
    viral_ratio_sum = 0.0
    ad_safe_ratio_sum = 0.0
    total_ad_safe_sum = 0

    for doc in rows_raw:
        keyword = doc.get("keyword") or "(unknown)"
        region_val = doc.get("region") or "ALL"

        videos_count = int(doc.get("videos_count") or 0)
        weak_viral = int(doc.get("weak_viral_count") or 0)
        viral = int(doc.get("viral_count") or 0)
        super_viral = int(doc.get("super_viral_count") or 0)

        viral_total = weak_viral + viral + super_viral

        views_sum = int(doc.get("views_sum_24h") or 0)
        views_min = int(doc.get("views_min_24h") or 0)
        views_max = int(doc.get("views_max_24h") or 0)

        likes_sum = int(doc.get("likes_sum_24h") or 0)
        likes_min = int(doc.get("likes_min_24h") or 0)
        likes_max = int(doc.get("likes_max_24h") or 0)

        comments_sum = int(doc.get("comments_sum_24h") or 0)
        comments_min = int(doc.get("comments_min_24h") or 0)
        comments_max = int(doc.get("comments_max_24h") or 0)

        ad_safe_count = int(doc.get("ad_safe_count") or 0)
        ad_risky_count = int(doc.get("ad_risky_count") or 0)

        # Derived metrics
        viral_ratio = (viral_total / videos_count) if videos_count > 0 else 0.0
        super_viral_ratio = (super_viral / videos_count) if videos_count > 0 else 0.0
        if videos_count > 0:
            raw_ad_safe_ratio = ad_safe_count / videos_count
            # clamp về [0, 1] để không bao giờ > 100%
            ad_safe_ratio = min(max(raw_ad_safe_ratio, 0.0), 1.0)
        else:
            ad_safe_ratio = 0.0
        ad_safe_ratio = (ad_safe_count / videos_count) if videos_count > 0 else 0.0

        avg_views = (views_sum / videos_count) if videos_count > 0 else 0.0
        avg_likes = (likes_sum / videos_count) if videos_count > 0 else 0.0
        avg_comments = (comments_sum / videos_count) if videos_count > 0 else 0.0

        total_videos_sum += videos_count
        viral_ratio_sum += viral_ratio
        ad_safe_ratio_sum += ad_safe_ratio
        total_ad_safe_sum += ad_safe_count

        prepared_rows.append(
            {
                "keyword": keyword,
                "region": region_val,
                "videos_count": videos_count,
                "weak_viral": weak_viral,
                "viral": viral,
                "super_viral": super_viral,
                "viral_total": viral_total,
                "viral_ratio": viral_ratio,
                "super_viral_ratio": super_viral_ratio,
                "views_sum": views_sum,
                "views_min": views_min,
                "views_max": views_max,
                "likes_sum": likes_sum,
                "likes_min": likes_min,
                "likes_max": likes_max,
                "comments_sum": comments_sum,
                "comments_min": comments_min,
                "comments_max": comments_max,
                "avg_views": avg_views,
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "ad_safe_count": ad_safe_count,
                "ad_risky_count": ad_risky_count,
                "ad_safe_ratio": ad_safe_ratio,
                "last_updated": doc.get("last_updated"),
            }
        )

    # Sort trong Python theo metric mong muốn
    if sort_by == "avg_views":
        prepared_rows.sort(key=lambda r: r["avg_views"], reverse=True)
    elif sort_by == "videos":
        prepared_rows.sort(key=lambda r: r["videos_count"], reverse=True)
    elif sort_by == "super_viral_ratio":
        prepared_rows.sort(key=lambda r: r["super_viral_ratio"], reverse=True)
    elif sort_by == "ad_safe_ratio":
        prepared_rows.sort(key=lambda r: r["ad_safe_ratio"], reverse=True)
    else:
        # default: viral_ratio
        prepared_rows.sort(key=lambda r: r["viral_ratio"], reverse=True)

    # Apply limit
    prepared_rows = prepared_rows[:limit]

    total_keywords = len(prepared_rows)
    avg_viral_ratio = (viral_ratio_sum / total_keywords) if total_keywords > 0 else 0.0
    avg_ad_safe_ratio = (
        (total_ad_safe_sum / total_videos_sum) if total_videos_sum > 0 else 0.0
    )

    summary = {
        "total_keywords": total_keywords,
        "total_videos": total_videos_sum,
        "avg_viral_ratio": avg_viral_ratio,
        "avg_ad_safe_ratio": avg_ad_safe_ratio,
    }

    return {
        "rows": prepared_rows,
        "summary": summary,
    }


def _videos_coll():
    db = get_db()
    return db["videos"]


def load_filter_keywords() -> List[str]:
    """
    Prefer keywords snapshot in dashboard_kpis.filter_keywords
    then fallback to aggregation on videos.source.query.
    """
    db = get_db()
    kpis = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    if kpis and "filter_keywords" in kpis:
        kws = kpis.get("filter_keywords") or []
        return [d.get("query") for d in kws if d.get("query")]

    coll = _videos_coll()
    pipeline = [
        {"$match": {"source.query": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$source.query", "video_count": {"$sum": 1}}},
        {"$sort": {"video_count": -1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline)]


def load_filter_regions() -> List[str]:
    """
    Prefer regions snapshot in dashboard_kpis.filter_regions
    then fallback to aggregation on videos.source.regionCode.
    """
    db = get_db()
    kpis = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    if kpis and "filter_regions" in kpis:
        regs = kpis.get("filter_regions") or []
        return [d.get("region") for d in regs if d.get("region")]

    coll = _videos_coll()
    pipeline = [
        {"$match": {"source.regionCode": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$source.regionCode"}},
        {"$sort": {"_id": 1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline) if doc["_id"]]


def query_videos(filters: Dict[str, Any], page: int, page_size: int):
    """
    Query videos by Mongo filters with pagination.
    Returns (rows, total_count).
    """
    coll = _videos_coll()

    total = coll.count_documents(filters)
    if total == 0:
        return [], 0

    skip = (page - 1) * page_size
    if skip >= total:
        skip = max(0, (max(1, (total - 1) // page_size + 1) - 1) * page_size)

    projection = {
        "_id": 1,
        "video_id": 1,
        "videoId": 1,
        "title": 1,
        "snippet.title": 1,
        "source.title": 1,
        "tracking.status": 1,
        "ml_flags.viral_v2.final.status": 1,
        "ml_flags.viral_v2.final.behavior": 1,
    }

    cursor = (
        coll.find(filters, projection=projection)
        .sort([("latest_stats_ts", -1), ("_id", -1)])
        .skip(skip)
        .limit(page_size)
    )

    rows: List[Dict[str, Any]] = []
    for doc in cursor:
        source = doc.get("source") or {}
        snippet = doc.get("snippet") or {}
        tracking = doc.get("tracking") or {}
        ml_flags = doc.get("ml_flags") or {}
        viral_v2 = ml_flags.get("viral_v2") or {}
        final_info = viral_v2.get("final") or {}

        video_id = (
            doc.get("video_id")
            or doc.get("videoId")
            or str(doc.get("_id"))
        )

        title = (
            doc.get("title")
            or snippet.get("title")
            or source.get("title")
            or "(no title)"
        )

        raw_status = final_info.get("status")
        raw_behavior = final_info.get("behavior")

        status_label = STATUS_LABELS.get(
            raw_status, raw_status or "Unknown / no decision"
        )
        behavior_label = BEHAVIOR_LABELS.get(
            raw_behavior, raw_behavior or "Unknown"
        )

        raw_tracking = tracking.get("status") or "unknown"
        tracking_label = TRACKING_STATUS_LABELS.get(raw_tracking, raw_tracking)

        rows.append(
            {
                "video_id": video_id,
                "title": title,
                "tracking_status": tracking_label,
                "final_status": status_label,
                "behavior": behavior_label,
                "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
                "analytics_url": f"/video-analytics/{video_id}",
            }
        )

    return rows, total


def _parse_iso_ts(value: str) -> datetime | None:
    """Parse ISO timestamp string (handles ...Z and +00:00)."""
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Convert datetime to UTC-aware (or keep None)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Mongo thường trả datetime naive -> gắn UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

# -----------------------------
# Worker tracking helpers
# -----------------------------

DEFAULT_WORKER_INTERVAL_MIN: Dict[str, int] = {
    # Bạn chỉnh lại cho khớp tên worker thực tế nếu khác
    "discover_once": 10,
    "track_once": 10,
    "lowq_3h": 20,
    "lowq_6h": 20,
    "viral_6h": 30,
    "viral_12h": 30,
    "viral_24h": 30,
    "viral_finalize": 30,
    "ad_safety": 30,
    "_default": 30,
}


def load_worker_status() -> List[Dict[str, Any]]:
    """
    Load worker heartbeat docs from MongoDB and classify status.

    Expect collection `worker_heartbeats` với schema gợi ý:
    {
        "_id": "discover_once",
        "worker": "discover_once",
        "host": "vps-01",
        "last_heartbeat": <datetime hoặc {$date: "..."} hoặc string ISO>,
        "expected_interval_min": 10,
        "last_error": "optional..."
    }
    """
    db = get_db()
    coll = db["worker_heartbeats"]
    now = datetime.now(timezone.utc)

    items: List[Dict[str, Any]] = []

    for doc in coll.find({}):
        name = doc.get("worker") or doc.get("name") or str(doc.get("_id") or "unknown")
        host = doc.get("host") or "-"

        raw_ts = (
            doc.get("last_heartbeat")
            or doc.get("last_seen")
            or doc.get("last_run")
        )

        # Dùng _parse_iso_ts + _ensure_utc đã có sẵn trong file
        if isinstance(raw_ts, datetime):
            last_dt = _ensure_utc(raw_ts)
        else:
            last_dt = _ensure_utc(_parse_iso_ts(raw_ts)) if raw_ts else None

        minutes_ago = None
        if last_dt is not None:
            minutes_ago = (now - last_dt).total_seconds() / 60.0

        expected = doc.get("expected_interval_min") or DEFAULT_WORKER_INTERVAL_MIN.get(
            name, DEFAULT_WORKER_INTERVAL_MIN["_default"]
        )

        # Phân loại trạng thái
        if minutes_ago is None:
            status_level = "unknown"
            status_label = "Unknown"
        elif minutes_ago <= expected * 2:
            status_level = "ok"
            status_label = "Healthy"
        elif minutes_ago <= expected * 5:
            status_level = "warning"
            status_label = "Delayed"
        else:
            status_level = "error"
            status_label = "Offline"

        # Map sang icon + Tailwind class để Jinja render đẹp
        if status_level == "ok":
            status_icon = "✅"
            status_class = "bg-emerald-500/15 border border-emerald-400/60 text-emerald-200"
        elif status_level == "warning":
            status_icon = "⚠️"
            status_class = "bg-amber-500/15 border border-amber-400/60 text-amber-200"
        elif status_level == "error":
            status_icon = "⛔"
            status_class = "bg-rose-500/15 border border-rose-400/60 text-rose-200"
        else:
            status_icon = "❔"
            status_class = "bg-slate-500/15 border border-slate-400/60 text-slate-200"

        if last_dt is not None:
            last_str = last_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            last_str = "-"

        if minutes_ago is not None:
            lag_str = f"{minutes_ago:.1f} min ago"
        else:
            lag_str = "-"

        items.append(
            {
                "name": name,
                "host": host,
                "status_level": status_level,
                "status_label": status_label,
                "status_icon": status_icon,
                "status_class": status_class,
                "last_heartbeat_str": last_str,
                "lag_str": lag_str,
                "expected_interval_min": expected,
                "last_error": doc.get("last_error") or "",
            }
        )

    # Sort theo tên để bảng gọn
    items.sort(key=lambda x: x["name"])
    return items


def get_video_analytics(video_id: str) -> Dict[str, Any] | None:
    """
    Load one video doc and prepare analytics data for charts.
    - Age of video
    - Time curve for views / likes / comments
    - Ad-friendly + viral status + behavior
    """
    coll = _videos_coll()

    # Try several keys (_id is usually the videoId string)
    doc = coll.find_one({"_id": video_id})
    if doc is None:
        doc = coll.find_one({"video_id": video_id})
    if doc is None:
        doc = coll.find_one({"videoId": video_id})
    if doc is None:
        return None

    snippet = doc.get("snippet") or {}
    source = doc.get("source") or {}
    tracking = doc.get("tracking") or {}
    ml_flags = doc.get("ml_flags") or {}
    viral_v2 = ml_flags.get("viral_v2") or {}
    final_info = viral_v2.get("final") or {}
    ad_flag = ml_flags.get("ad_friendly_v1") or {}

    # Basic fields
    title = snippet.get("title") or "(no title)"
    channel_id = snippet.get("channelId")
    published_str = snippet.get("publishedAt")
    published_at = _parse_iso_ts(published_str) if published_str else None
    published_at = _ensure_utc(published_at)

    # Latest stats timestamp
    latest_ts = doc.get("latest_stats_ts")
    if isinstance(latest_ts, datetime):
        last_ts = latest_ts
    elif isinstance(latest_ts, dict) and "$date" in latest_ts:
        last_ts = _parse_iso_ts(latest_ts["$date"])
    else:
        last_ts = None

    # Fallback last_ts = last point in stats_snapshots
    snapshots = doc.get("stats_snapshots") or []
    if snapshots and last_ts is None:
        last_ts = _parse_iso_ts(snapshots[-1].get("ts"))

    last_ts = _ensure_utc(last_ts)

    # Compute age
    age_hours = None
    age_days = None
    human_age = "Unknown"
    if published_at and last_ts:
        delta = last_ts - published_at
        age_hours = delta.total_seconds() / 3600.0
        age_days = delta.total_seconds() / 86400.0
        human_age = f"{age_hours:.1f} hours (~{age_days:.1f} days)"
    elif published_at:
        delta = datetime.now(timezone.utc) - published_at
        age_hours = delta.total_seconds() / 3600.0
        age_days = delta.total_seconds() / 86400.0
        human_age = f"{age_hours:.1f} hours (~{age_days:.1f} days)"

    # Prepare time-series
    labels_hours: list[float] = []
    views: list[int] = []
    likes: list[int] = []
    comments: list[int] = []

    if published_at and snapshots:
        for snap in snapshots:
            ts_str = snap.get("ts")
            ts = _parse_iso_ts(ts_str) if ts_str else None
            ts = _ensure_utc(ts)
            if not ts or not published_at:
                continue

            hours = (ts - published_at).total_seconds() / 3600.0
            if hours < 0:
                continue

            labels_hours.append(round(hours, 2))
            views.append(int(snap.get("viewCount") or 0))
            likes.append(int(snap.get("likeCount") or 0))
            comments.append(int(snap.get("commentCount") or 0))

    total_views = views[-1] if views else None
    total_likes = likes[-1] if likes else None
    total_comments = comments[-1] if comments else None

    # Friendly labels
    raw_status = final_info.get("status")
    raw_behavior = final_info.get("behavior")
    viral_status_label = STATUS_LABELS.get(
        raw_status, raw_status or "No decision yet"
    )
    behavior_label = BEHAVIOR_LABELS.get(
        raw_behavior, raw_behavior or "Unknown"
    )

    # Ad-friendly
    raw_ad_label = ad_flag.get("label")
    ad_label = AD_LABELS.get(raw_ad_label, raw_ad_label or "Unknown")
    ad_score = ad_flag.get("score")

    # ==========================
    # Tracking (đã PATCH)
    # ==========================
    final_tracking_status = final_info.get("tracking_status_at_final")
    final_stop_reason = final_info.get("tracking_stop_reason_at_final")

    # Lấy stop_reason thực tế: ưu tiên final_*, nếu không thì tracking.stop_reason
    raw_stop_reason = final_stop_reason or tracking.get("stop_reason")
    tracking_stop_reason = TRACKING_REASON_LABELS.get(
        raw_stop_reason, raw_stop_reason
    )

    # Fallback an toàn:
    # Nếu đã có stop_reason -> chắc chắn tracking đã kết thúc => ép về "complete"
    if raw_stop_reason:
        raw_tracking_status = "complete"
    else:
        # Chưa có stop reason: dùng status mới nhất (final trước, rồi tới tracking)
        raw_tracking_status = (
            final_tracking_status or tracking.get("status") or "unknown"
        )

    tracking_status = TRACKING_STATUS_LABELS.get(
        raw_tracking_status, raw_tracking_status
    )

    return {
        "video_id": video_id,
        "title": title,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "channel_id": channel_id,
        "published_at": published_str,
        "human_age": human_age,
        "age_hours": age_hours,
        "age_days": age_days,
        "source_query": source.get("query"),
        "source_region": source.get("regionCode"),
        "viral_status_label": viral_status_label,
        "behavior_label": behavior_label,
        "tracking_status": tracking_status,
        "tracking_stop_reason": tracking_stop_reason,
        "ad_label": ad_label,
        "ad_score": ad_score,
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "timeline": {
            "labels": labels_hours,
            "views": views,
            "likes": likes,
            "comments": comments,
        },
    }



# ============================================================
# Routes
# ============================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard_root(request: Request):
    kpis = load_dashboard_kpis()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "kpis": kpis}
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    kpis = load_dashboard_kpis()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "kpis": kpis}
    )


@app.get("/api/kpis")
async def api_kpis():
    return load_dashboard_kpis()


@app.get("/viral-filter", response_class=HTMLResponse)
async def viral_filter_page(
    request: Request,
    keyword: str = Query("(All)"),
    region: str = Query("(All)"),
    final: str = Query("(All)"),
    behavior: str = Query("(All)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
):
    """
    External viral filter page – mirrors the logic of Streamlit 03_filter,
    but rendered server-side with Jinja.
    """
    keywords = load_filter_keywords()
    regions = load_filter_regions()

    keyword_options = ["(All)"] + keywords
    region_options = ["(All)"] + regions

    # ----- FILTER LOGIC -----
    has_kw = keyword != "(All)"
    has_rg = region != "(All)"
    has_vl = final != "(All)"
    has_bh = behavior != "(All)"

    filters: Dict[str, Any] = {}

    # Keyword filter
    if has_kw:
        filters["source.query"] = keyword

    # Region filter
    if has_rg:
        filters["source.regionCode"] = region

    # Final viral status filter
    if has_vl:
        key = "ml_flags.viral_v2.final.status"
        text = final.lower()

        # Explosive Growth (super viral)
        if "explosive" in text or "super viral" in text:
            filters[key] = "super_viral"

        # Going Viral (viral)
        elif "going viral" in text or "(viral)" in text:
            filters[key] = "viral"

        # Early Growth (weak viral)
        elif "early growth" in text or "weak viral" in text:
            filters[key] = "weak_viral"

        # No Viral Signal -> non-viral, low-quality non-viral, or unknown
        elif "no viral signal" in text:
            filters[key] = {
                "$in": ["non_viral", "non_viral_lowq", "unknown", None]
            }

    # Behavior pattern filter
    if has_bh:
        key = "ml_flags.viral_v2.final.behavior"
        map_behavior = {
            "No signal": "no_signal",
            "Early peak": "early_peak",
            "Late growth": "late_growth",
            "Consistent": "consistent",
            "Volatile": "volatile",
            "Neutral / unclear": "neutral",
        }
        filters[key] = map_behavior.get(behavior)

    rows: List[Dict[str, Any]] = []
    total = 0
    max_page = 1

    if has_kw or has_rg or has_vl or has_bh:
        rows, total = query_videos(filters, page, page_size)
        if total > 0:
            max_page = max(1, (total - 1) // page_size + 1)
            if page > max_page:
                page = max_page

    from urllib.parse import urlencode

    base_params = {
        "keyword": keyword,
        "region": region,
        "final": final,
        "behavior": behavior,
        "page_size": page_size,
    }

    prev_url = None
    next_url = None
    if total > 0:
        if page > 1:
            prev_url = "/viral-filter?" + urlencode(
                {**base_params, "page": page - 1}
            )
        if page < max_page:
            next_url = "/viral-filter?" + urlencode(
                {**base_params, "page": page + 1}
            )

    context = {
        "request": request,
        "keyword_options": keyword_options,
        "region_options": region_options,
        "viral_options": VIRAL_OPTIONS,
        "behavior_options": BEHAVIOR_OPTIONS,
        "selected_keyword": keyword,
        "selected_region": region,
        "selected_final": final,
        "selected_behavior": behavior,
        "page_size": page_size,
        "has_any_filter": has_kw or has_rg or has_vl or has_bh,
        "rows": rows,
        "total": total,
        "page": page,
        "max_page": max_page,
        "prev_url": prev_url,
        "next_url": next_url,
    }

    return templates.TemplateResponse("viral_filter.html", context)


@app.get("/video-analytics/{video_id}", response_class=HTMLResponse)
async def video_analytics_page(request: Request, video_id: str):
    """
    Show analytics for a single video: age, time curve for views/likes/comments,
    and ad-friendly / viral information.
    """
    data = get_video_analytics(video_id)
    if not data:
        raise HTTPException(status_code=404, detail="Video not found")

    return templates.TemplateResponse(
        "video_analytics.html",
        {
            "request": request,
            "data": data,
        },
    )

@app.get("/keywords-analysis", response_class=HTMLResponse)
async def keywords_analysis_page(
    request: Request,
    region: str = Query("ALL"),
    min_videos: int = Query(10, ge=0),
    sort_by: str = Query("viral_ratio"),
    limit: int = Query(50, ge=10, le=200),
):
    """
    Trang Keywords Analysis: phân tích keyword dựa trên collection keyword_stats
    (all_time, per region).
    """
    regions = load_keyword_regions_for_stats()

    # Nếu region user chọn không tồn tại (vd gõ tay), fallback về ALL nếu có
    if region not in regions and regions:
        region = "ALL" if "ALL" in regions else regions[0]

    data = load_keyword_stats_view(
        region=region,
        min_videos=min_videos,
        limit=limit,
        sort_by=sort_by,
    )

    context = {
        "request": request,
        "regions": regions,
        "selected_region": region,
        "min_videos": min_videos,
        "sort_by": sort_by,
        "limit": limit,
        "rows": data["rows"],
        "summary": data["summary"],
    }

    return templates.TemplateResponse("keyword_analysis.html", context)

# -------------------------------------------------
# Worker tracking (using worker_runs collection)
# -------------------------------------------------
from datetime import datetime, timezone
from typing import List, Dict, Any
from collections import defaultdict

from config.db import get_db  # dùng DB chung

# Expected interval (minutes) cho từng worker
WORKER_INTERVAL_MIN: Dict[str, int] = {
    "discover_once": 15,
    "track_once": 15,
    "low_quality_autoflag_3h": 30,
    "low_quality_autoflag_6h": 30,
    "viral_scoring_h6": 60,
    "viral_scoring_h12": 90,
    "viral_scoring_h24": 180,
    "viral_finalize": 60,
    "ad_friendly_v1": 60,
    "compute_dashboard_kpis": 60,
    "keyword_stats_all_time": 1,
    "_default": 60,
}

# Tên hiển thị đẹp hơn
WORKER_DISPLAY_NAME: Dict[str, str] = {
    "discover_once": "Discovery queue",
    "track_once": "Tracking queue",
    "low_quality_autoflag_3h": "Low-quality filter · 3h model",
    "low_quality_autoflag_6h": "Low-quality filter · 6h model",
    "viral_scoring_h6": "Viral scoring · 6h model",
    "viral_scoring_h12": "Viral scoring · 12h model",
    "viral_scoring_h24": "Viral scoring · 24h model",
    "viral_finalize": "Viral finalize & label writer",
    "ad_friendly_v1": "Ad-safety scoring · v1",
    "compute_dashboard_kpis": "Dashboard KPIs refresher",
    "keyword_stats_all_time": "Keyword Stats · all-time (incremental)",
}

# Description ngắn cho từng worker
WORKER_DESCRIPTION: Dict[str, str] = {
    "discover_once": "Discover new videos from search queries and seed channels.",
    "track_once": "Refresh statistics for active videos that are still being tracked.",
    "low_quality_autoflag_3h": "Run the 3-hour model to auto-flag very low-potential videos.",
    "low_quality_autoflag_6h": "Run the 6-hour model to prune low-potential videos from tracking.",
    "viral_scoring_h6": "Score early virality using the 6-hour model.",
    "viral_scoring_h12": "Score virality using the 12-hour model (mid-stage).",
    "viral_scoring_h24": "Score virality using the 24-hour model (late-stage).",
    "viral_finalize": "Combine model outputs and write the final viral label for videos.",
    "ad_friendly_v1": "Evaluate videos for ad-friendliness using the ad-safety model.",
    "compute_dashboard_kpis": "Aggregate KPIs for the internal dashboard.",
    "keyword_stats_all_time": (
        "Aggregate per-keyword 24h metrics, viral labels and region stats "
        "into the `keyword_stats` collection."
    ),
}

# Metric nào nên show cho từng worker (những cái nhỏ lẻ khác sẽ ẩn)
METRIC_WHITELIST: Dict[str, List[str]] = {
    "discover_once": ["pages", "total_found", "total_upserted", "reason", "error"],
    "track_once": ["completed", "processed"],
    "low_quality_autoflag_3h": ["low_3h", "total_candidates", "total_docs", "total_updated"],
    "low_quality_autoflag_6h": ["low_6h", "total_candidates", "total_docs", "total_updated"],
    "viral_scoring_h6": ["docs_scanned", "docs_updated"],
    "viral_scoring_h12": ["docs_scanned", "docs_updated"],
    "viral_scoring_h24": ["docs_scanned", "docs_updated"],
    "viral_finalize": ["finalized", "processed"],
    "ad_friendly_v1": ["docs_scanned", "docs_updated"],
    "compute_dashboard_kpis": [],
    "keyword_stats_all_time": [          
        "videos_processed",
        "keywords_updated",
        "duration_sec",
    ],
}

# Đổi tên metric cho dễ đọc
METRIC_LABEL_OVERRIDES: Dict[str, str] = {
    "docs_scanned": "docs scanned",
    "docs_updated": "docs updated",
    "total_found": "found",
    "total_upserted": "upserted",
    "total_candidates": "candidates",
    "total_docs": "docs",
    "total_updated": "updated",
    "low_3h": "low @3h",
    "low_6h": "low @6h",
    "completed": "completed batches",
}


def _group_for_worker(name: str) -> str:
    """Human friendly group name for each worker."""
    if name in ("discover_once", "track_once"):
        return "Ingestion & tracking"
    if name.startswith("low_quality_autoflag"):
        return "Low-quality filters"
    if name.startswith("viral_scoring"):
        return "Viral scoring models"
    if name == "viral_finalize":
        return "Viral finalize"
    if name == "ad_friendly_v1":
        return "Ad-safety model"
    if name == "compute_dashboard_kpis":
        return "Dashboard KPIs"
    if name == "keyword_stats_all_time":
        return "Aggregations & Stats"
    return "Other workers"


def _parse_worker_last_run(raw: Any) -> datetime | None:
    """Handle datetime from Mongo (datetime) hoặc string ISO."""
    if raw is None:
        return None

    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _classify_worker_status(
    status: str | None, minutes_ago: float | None, expected_min: int
) -> tuple[str, str, str, str, str]:
    """
    Map to (level, label, icon, badge_class, card_border_class).

    level ∈ {"ok","warning","error","unknown"}
    """
    base = (status or "").lower()

    if minutes_ago is None:
        level = "unknown"
        label = "Unknown"
    else:
        # Staleness based on expected interval
        if minutes_ago > expected_min * 4:
            level = "error"
            label = "Offline"
        elif minutes_ago > expected_min * 2:
            level = "warning"
            label = "Delayed"
        else:
            # Fresh enough -> dùng status của worker
            if "err" in base or "fail" in base:
                level = "error"
                label = "Error"
            elif "run" in base and "ok" not in base:
                level = "ok"
                label = "Running"
            elif "ok" in base or "success" in base or "done" in base or base == "":
                level = "ok"
                label = "Healthy"
            else:
                level = "unknown"
                label = status or "Unknown"

    if level == "ok":
        icon = "✅"
        badge = "bg-emerald-500/15 border border-emerald-400/60 text-emerald-100"
        card = "border border-emerald-500/40"
    elif level == "warning":
        icon = "⚠️"
        badge = "bg-amber-500/15 border border-amber-400/60 text-amber-100"
        card = "border border-amber-500/40"
    elif level == "error":
        icon = "⛔"
        badge = "bg-rose-500/15 border border-rose-400/60 text-rose-100"
        card = "border border-rose-500/40"
    else:
        icon = "❔"
        badge = "bg-slate-500/15 border border-slate-400/60 text-slate-100"
        card = "border border-slate-700"

    return level, label, icon, badge, card


def load_worker_tracking_data() -> Dict[str, Any]:
    """
    Read worker_runs collection and build:
    - workers: list of normalized worker dicts
    - groups: list[{name, workers}]
    - summary counts
    """
    db = get_db()
    coll = db["worker_runs"]

    docs = list(coll.find({}).sort("last_run", -1).limit(500))

    now = datetime.now(timezone.utc)
    seen_names: set[str] = set()
    workers: List[Dict[str, Any]] = []

    for doc in docs:
        name = doc.get("name") or "unknown"
        if name in seen_names:
            continue
        seen_names.add(name)

        last_dt = _parse_worker_last_run(doc.get("last_run"))
        if last_dt is not None:
            minutes_ago = (now - last_dt).total_seconds() / 60.0
            last_run_str = last_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            lag_str = f"{minutes_ago:.1f} min ago"
        else:
            minutes_ago = None
            last_run_str = "-"
            lag_str = "-"

        expected_min = WORKER_INTERVAL_MIN.get(name, WORKER_INTERVAL_MIN["_default"])
        level, label, icon, badge, card_border = _classify_worker_status(
            doc.get("status"), minutes_ago, expected_min
        )

        display_name = WORKER_DISPLAY_NAME.get(name, name)
        description = WORKER_DESCRIPTION.get(name, "")

        # Lọc metrics
        allowed_keys = METRIC_WHITELIST.get(name)
        metrics: List[Dict[str, Any]] = []
        for key, value in doc.items():
            if key in ("_id", "name", "last_run", "status"):
                continue
            if allowed_keys is not None and key not in allowed_keys:
                continue

            label_text = METRIC_LABEL_OVERRIDES.get(key, key.replace("_", " "))
            metrics.append(
                {
                    "label": label_text,
                    "value": value,
                }
            )

        workers.append(
            {
                "name": name,
                "display_name": display_name,
                "description": description,
                "group": _group_for_worker(name),
                "status_level": level,
                "status_label": label,
                "status_icon": icon,
                "status_badge_class": badge,
                "card_border_class": card_border,
                "last_run_str": last_run_str,
                "lag_str": lag_str,
                "expected_interval_min": expected_min,
                "metrics": metrics,
            }
        )

    total = len(workers)
    healthy = sum(1 for w in workers if w["status_level"] == "ok")
    delayed = sum(1 for w in workers if w["status_level"] == "warning")
    offline = sum(1 for w in workers if w["status_level"] == "error")
    unknown = sum(1 for w in workers if w["status_level"] == "unknown")

    groups_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for w in workers:
        groups_map[w["group"]].append(w)

    groups = []
    for group_name in sorted(groups_map.keys()):
        group_workers = sorted(groups_map[group_name], key=lambda x: x["display_name"])
        groups.append({"name": group_name, "workers": group_workers})

    return {
        "workers": workers,
        "groups": groups,
        "total": total,
        "healthy_count": healthy,
        "delayed_count": delayed,
        "offline_count": offline,
        "unknown_count": unknown,
    }


@app.get("/worker-tracking", response_class=HTMLResponse)
async def worker_tracking_page(request: Request):
    data = load_worker_tracking_data()
    data["request"] = request
    return templates.TemplateResponse("worker_tracking.html", data)

from typing import Dict, Any, List
# ...
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse
# KHÔNG cần JSONResponse, FastAPI tự trả JSON từ dict
# ...

from config.db import get_db

@app.get("/api/keyword-region-breakdown")
async def keyword_region_breakdown(keyword: str):
    """
    Trả về breakdown theo region cho 1 keyword.
    Dùng dữ liệu từ collection 'keyword_stats':
      - Bỏ qua region == 'ALL'
      - Dùng videos_count làm trọng số (có thể đổi sang views_sum_24h nếu thích)
    """
    db = get_db()
    coll = db["keyword_stats"]

    docs = list(
        coll.find(
            {
                "keyword": keyword,
                "region": {"$ne": "ALL"},
            },
            {
                "_id": 0,
                "region": 1,
                "videos_count": 1,
                "views_sum_24h": 1,
            },
        )
    )

    # Không có dữ liệu thì trả rỗng
    if not docs:
        return {
            "keyword": keyword,
            "regions": [],
            "videos": [],
            "views": [],
        }

    regions: List[str] = []
    videos: List[int] = []
    views: List[int] = []

    for d in docs:
        regions.append(d.get("region") or "UNK")
        videos.append(int(d.get("videos_count") or 0))
        views.append(int(d.get("views_sum_24h") or 0))

    return {
        "keyword": keyword,
        "regions": regions,
        "videos": videos,
        "views": views,
    }
