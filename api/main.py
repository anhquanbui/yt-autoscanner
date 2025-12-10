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

    # Tracking
    final_tracking_status = final_info.get("tracking_status_at_final")
    final_stop_reason = final_info.get("tracking_stop_reason_at_final")

    raw_tracking_status = (
        final_tracking_status or tracking.get("status") or "unknown"
    )
    tracking_status = TRACKING_STATUS_LABELS.get(
        raw_tracking_status, raw_tracking_status
    )

    raw_stop_reason = final_stop_reason or tracking.get("stop_reason")
    tracking_stop_reason = TRACKING_REASON_LABELS.get(
        raw_stop_reason, raw_stop_reason
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
