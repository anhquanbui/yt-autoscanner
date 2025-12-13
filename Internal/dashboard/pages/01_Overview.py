import streamlit as st
from datetime import datetime, timezone
import sys
from pathlib import Path

# Ensure project imports work (config/, dashboard/)
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.db import get_db
from dashboard.components.system_status import (
    render_system_status,
    load_worker_last_runs,
    load_worker_health,
)
from dashboard.components.sidebar_nav import render_sidebar_nav

# Sidebar
render_sidebar_nav()

# =========================
# Layout & global CSS
# =========================
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {
    background: #f3f4f6;
}

.main .block-container {
    max-width: 1200px;
    padding-top: 2.2rem;
    padding-left: 2.5rem;
    padding-right: 2.5rem;
    margin-left: auto;
    margin-right: auto;
}

h1 {
    font-weight: 700 !important;
    letter-spacing: -0.02em;
}
h2, h3 {
    font-weight: 600 !important;
}

.block-container hr {
    margin-top: 1.7rem;
    margin-bottom: 1.7rem;
}

/* Small caption tweak */
.block-container p, .block-container .stMarkdown {
    font-size: 0.95rem;
}

/* Metric cards */
.metric-card {
    background: #ffffff;
    padding: 18px 22px;
    border-radius: 14px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 3px 8px rgba(15,23,42,0.05);
    transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.metric-card:hover {
    box-shadow: 0 6px 16px rgba(15,23,42,0.12);
    transform: translateY(-1px);
}
.metric-title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.metric-title {
    font-size: 0.86rem;
    font-weight: 600;
    color: #6b7280;
}
.metric-value {
    font-size: 1.45rem;
    font-weight: 700;
    color: #111827;
}
.metric-progress-outer {
    margin-top: 6px;
    width: 100%;
    height: 6px;
    border-radius: 999px;
    background: #e5e7eb;
}
.metric-progress-inner {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, #4f46e5, #3b82f6);
}
.metric-percentage {
    font-size: 0.9rem;
    color: #6b7280;
    margin-top: 4px;
}

/* Extra subtext line under metric value (used for Viral breakdown) */
.metric-subtext {
    font-size: 0.85rem;
    color: #6b7280;
    margin-top: 4px;
}

/* Make the tiny archive buttons look more like icon buttons */
button[kind="secondary"] {
    padding: 0.25rem 0.5rem;
}
</style>
    """,
    unsafe_allow_html=True,
)

# =========================
# KPI loader
# =========================
@st.cache_data(ttl=10)
def load_kpis() -> dict:
    """Load latest KPIs from Mongo (dashboard_kpis)."""
    db = get_db()
    doc = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    base = {
        "total_videos": 0,
        "total_channels": 0,
        "tracking_active": 0,
        "completed_total": 0,
        "stopped_total": 0,
        "completed_age24": 0,
        "completed_removed": 0,
        "stopped_low_quality": 0,
        "low_quality_flagged": 0,
        "snapshot_ts": None,
        "viral2_h6_scored": 0,
        "viral2_h12_scored": 0,
        "viral2_h24_scored": 0,
        "viral2_stage_6h_only": 0,
        "viral2_stage_12h_only": 0,
        "viral2_stage_24h_only": 0,
        "viral2_final_weak_viral": 0,
        "viral2_final_viral": 0,
        "viral2_final_super_viral": 0,
        "viral2_final_nonviral": 0,
        "viral2_final_nonviral_lowq": 0,
        "viral2_final_unknown": 0,
        "viral2_final_decided": 0,
        "ad_friendly_total": 0,
        "non_ad_friendly_total": 0,
    }

    if not doc:
        return base

    for key in base:
        if key == "snapshot_ts":
            continue
        try:
            base[key] = int(doc.get(key, 0))
        except Exception:
            base[key] = 0

    if "low_quality_flagged_any" in doc:
        try:
            base["low_quality_flagged"] = int(doc.get("low_quality_flagged_any", 0))
        except Exception:
            base["low_quality_flagged"] = 0

    ts = doc.get("ts")
    if isinstance(ts, datetime):
        ts = ts.astimezone(timezone.utc)
        base["snapshot_ts"] = ts.strftime("%Y-%m-%d %H:%M UTC")
    elif ts:
        base["snapshot_ts"] = str(ts)

    return base

# =========================
# Archive helpers
# =========================
def _archive_by_query(label: str, query: dict) -> int:
    """Merge matching videos into videos_archived, then delete from videos."""
    db = get_db()
    videos = db.videos
    archived = db.videos_archived

    to_archive = videos.count_documents(query)
    if to_archive == 0:
        return 0

    pipeline = [
        {"$match": query},
        {
            "$merge": {
                "into": archived.name,
                "on": "_id",
                "whenMatched": "keepExisting",
                "whenNotMatched": "insert",
            }
        },
    ]
    list(videos.aggregate(pipeline, allowDiskUse=True))
    delete_result = videos.delete_many(query)
    return delete_result.deleted_count


def archive_removed_videos() -> int:
    """Archive 'removed/unavailable' completed videos."""
    query = {
        "tracking.status": {"$in": ["complete", "completed"]},
        "tracking.stop_reason": {
            "$regex": "(removed|unavailable)",
            "$options": "i",
        },
    }
    return _archive_by_query("removed/unavailable", query)


def archive_stopped_lowq_videos() -> int:
    """Archive 'stopped low-quality' videos."""
    query = {
        "tracking.status": "stopped",
        "tracking.stop_reason": {
            "$regex": "^ml\\.low_quality",
            "$options": "i",
        },
    }
    return _archive_by_query("stopped_low_quality", query)

# =========================
# Tracking metrics + archive actions
# =========================
def render_simple_metrics(kpis: dict):
    total_videos = kpis.get("total_videos", 0)

    def pct_value(v: int) -> float:
        return (v / total_videos * 100.0) if total_videos else 0.0

    def pct_str(v: int) -> str:
        return f"{pct_value(v):.2f}%"

    cards = [
        ("Tracking active", kpis["tracking_active"], None),
        ("Removed / Unavailable", kpis["completed_removed"], "removed"),
        ("Stopped (low quality)", kpis["stopped_low_quality"], "lowq"),
    ]

    c1, c2, c3 = st.columns(3)

    st.markdown(
        """
        <style>
        .archived-btn-wrapper button {
            background-color: transparent !important;
            color: #dc2626 !important;
            border: none !important;
            padding: 0 0 !important;
            font-size: 0.8rem !important;
            font-weight: 600 !important;
        }
        .archived-btn-wrapper button:hover {
            text-decoration: underline;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    for col, (title, value, archive_type) in zip([c1, c2, c3], cards):
        with col:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-title">{title}</div>
                    <div class="metric-value">{value:,}</div>
                    <div class="metric-progress-outer">
                        <div class="metric-progress-inner" style="width:{pct_value(value):.2f}%"></div>
                    </div>
                    <div class="metric-percentage">{pct_str(value)} of all videos</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if archive_type is not None:
                st.markdown(
                    "<div class='archived-btn-wrapper' "
                    "style='text-align:right; margin-top:4px; margin-bottom:4px;'>",
                    unsafe_allow_html=True,
                )
                btn_key = f"archive_{archive_type}"
                if st.button("Archived", key=btn_key, help="Move videos to videos_archived"):
                    if archive_type == "removed":
                        deleted = archive_removed_videos()
                        msg = f"Archived & deleted {deleted:,} removed/unavailable videos."
                    else:
                        deleted = archive_stopped_lowq_videos()
                        msg = f"Archived & deleted {deleted:,} low-quality videos."
                    load_kpis.clear()
                    st.success(msg)
                    st.experimental_rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='height: 8px'></div>", unsafe_allow_html=True)

# =========================
# Viral summary
# =========================
def render_viral_summary(kpis: dict):
    total_videos = kpis.get("total_videos", 0)

    def pct(v): return (v / total_videos * 100.0) if total_videos else 0.0
    def pct_str(v): return f"{pct(v):.2f}%"

    weak_viral = kpis.get("viral2_final_weak_viral", 0)
    mid_viral = kpis.get("viral2_final_viral", 0)
    super_viral = kpis.get("viral2_final_super_viral", 0)

    viral_total = weak_viral + mid_viral + super_viral
    non_total = kpis.get("viral2_final_nonviral", 0)
    unknown_total = kpis.get("viral2_final_unknown", 0)

    c1, c2, c3 = st.columns(3)
    cols = [c1, c2, c3]
    titles = ["Viral", "Non-viral", "Unknown / No decision"]
    values = [viral_total, non_total, unknown_total]

    for idx, col in enumerate(cols):
        title = titles[idx]
        value = values[idx]

        if idx == 0:
            breakdown_html = (
                f'<div class="metric-subtext">'
                f'Weak: {weak_viral:,} • Viral: {mid_viral:,} • Super: {super_viral:,}'
                f"</div>"
            )
        else:
            breakdown_html = ""

        html = f"""
<div class="metric-card">
  <div class="metric-title">{title}</div>
  <div class="metric-value">{value:,}</div>
  {breakdown_html}
  <div class="metric-progress-outer">
    <div class="metric-progress-inner" style="width:{pct(value):.2f}%"></div>
  </div>
  <div class="metric-percentage">{pct_str(value)} of all videos</div>
</div>
"""
        col.markdown(html, unsafe_allow_html=True)

# =========================
# Ad-friendly summary
# =========================
def render_ad_friendly_summary(kpis: dict):
    total_videos = kpis.get("total_videos", 0)
    ad_total = kpis.get("ad_friendly_total", 0)
    non_ad_total = kpis.get("non_ad_friendly_total", 0)

    def pct(v): return (v / total_videos * 100.0) if total_videos else 0.0
    def pct_str(v): return f"{pct(v):.2f}%"

    c1, c2 = st.columns(2)
    cards = [
        ("Ad-friendly videos", ad_total),
        ("Non ad-friendly / risky", non_ad_total),
    ]

    for col, (title, value) in zip([c1, c2], cards):
        col.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">{title}</div>
                <div class="metric-value">{value:,}</div>
                <div class="metric-progress-outer">
                    <div class="metric-progress-inner" style="width:{pct(value):.2f}%"></div>
                </div>
                <div class="metric-percentage">{pct_str(value)} of all videos</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# =========================
# Page body
# =========================
st.title("📊 YouTube AutoScanner — Overview (Local)")
st.subheader("📌 System KPIs")

if st.button("🔄 Refresh now"):
    load_kpis.clear()
    st.experimental_rerun()

kpis = load_kpis()

if kpis.get("snapshot_ts"):
    st.caption(f"Last KPI snapshot: {kpis['snapshot_ts']}")

c1, c2 = st.columns(2)
c1.metric("Total Videos", f"{kpis['total_videos']:,}")
c2.metric("Total Channels", f"{kpis['total_channels']:,}")

st.markdown("---")

st.markdown("### 🎯 Tracking & Completion Overview")
render_simple_metrics(kpis)

st.markdown("<div style='height: 1.5rem'></div>", unsafe_allow_html=True)

st.markdown("### 🛡️ Ad-Friendly / Brand Safety Summary")
render_ad_friendly_summary(kpis)

st.markdown("<div style='height: 1.5rem'></div>", unsafe_allow_html=True)

st.markdown("### 🚀 Viral Prediction Summary")
render_viral_summary(kpis)

st.markdown("---")
st.caption("YouTube AutoScanner — Overview Dashboard (Local dev)")
