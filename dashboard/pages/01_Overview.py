import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from config.db import get_db, _resolve_db_name


# =============== GLOBAL LAYOUT & THEME ===============
st.markdown(
    """
<style>
/* App background */
[data-testid="stAppViewContainer"] {
    background: #f3f4f6;
}

/* Center main content + nicer paddings */
.main .block-container {
    max-width: 1200px;
    padding-top: 2.2rem;
    padding-left: 2.5rem;
    padding-right: 2.5rem;
    margin-left: auto;
    margin-right: auto;
}

/* Headings */
h1 {
    font-weight: 700 !important;
    letter-spacing: -0.02em;
}
h2, h3 {
    font-weight: 600 !important;
}

/* Horizontal rule spacing */
.block-container hr {
    margin-top: 1.7rem;
    margin-bottom: 1.7rem;
}

/* Dataframe “card” look */
[data-testid="stDataFrame"] {
    border-radius: 12px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 2px 5px rgba(15,23,42,0.04);
    background: #ffffff;
}

/* Small caption tweak */
.block-container p, .block-container .stMarkdown {
    font-size: 0.95rem;
}
</style>
    """,
    unsafe_allow_html=True,
)


# =============== DATA LOADERS ===============

@st.cache_data(ttl=10)
def load_kpis() -> dict:
    """
    Load latest KPI snapshot from `dashboard_kpis`.
    """
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
    }

    if not doc:
        return base

    for key in [
        "total_videos",
        "total_channels",
        "tracking_active",
        "completed_total",
        "stopped_total",
        "completed_age24",
        "completed_removed",
        "stopped_low_quality",
    ]:
        value = doc.get(key, 0)
        try:
            base[key] = int(value)
        except Exception:
            base[key] = 0

    ts = doc.get("ts")
    if isinstance(ts, datetime):
        ts = ts.astimezone(timezone.utc)
        base["snapshot_ts"] = ts.strftime("%Y-%m-%d %H:%M UTC")
    elif ts is not None:
        base["snapshot_ts"] = str(ts)

    return base


@st.cache_data(ttl=60)
def load_worker_last_runs():
    """
    Read latest run timestamps for each worker from `worker_runs`.
    """
    db = get_db()

    workers = [
        ("discover_once", "Discover new videos"),
        ("track_once", "Track stats & snapshots"),
        ("low_quality_autoflag", "ML low-quality scoring"),
        ("compute_dashboard_kpis", "Dashboard KPI snapshot"),
    ]

    now = datetime.now(timezone.utc)
    results = []

    for key, label in workers:
        doc = db.worker_runs.find_one({"name": key}, sort=[("last_run", -1)])
        if doc and "last_run" in doc:
            ts = doc["last_run"]

            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                diff = now - ts
                sec = diff.total_seconds()

                if sec < 60:
                    pretty = f"{int(sec)}s ago"
                elif sec < 3600:
                    pretty = f"{int(sec // 60)}m ago"
                elif sec < 86400:
                    pretty = f"{int(sec // 3600)}h ago"
                else:
                    days = int(sec // 86400)
                    pretty = f"{days}d ago"
            else:
                pretty = str(ts)
        else:
            pretty = "No data"

        results.append({"Worker": label, "Last run": pretty})

    return results


# =============== SIMPLE METRIC STRIP ===============

def render_simple_metrics(kpis: dict):
    """
    Render 4 metrics as cards, including percentage of total videos
    and a small progress bar in each card.
    """
    total_videos = kpis.get("total_videos", 0)

    def pct_value(value: int) -> float:
        if total_videos == 0:
            return 0.0
        return value / total_videos * 100.0

    def pct_str(value: int) -> str:
        return f"{pct_value(value):.2f}%"

    card_css = """
    <style>
    .metric-card {
        background: #ffffff;
        padding: 18px 22px;
        border-radius: 14px;
        border: 1px solid #e5e7eb;
        box-shadow: 0 3px 8px rgba(15,23,42,0.05);
        transition: box-shadow 0.2s ease, transform 0.2s ease, border-color 0.2s ease;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        min-height: 120px;
    }
    .metric-card:hover {
        box-shadow: 0 6px 16px rgba(15,23,42,0.12);
        transform: translateY(-1px);
        border-color: #c7d2fe;
    }
    .metric-title {
        font-size: 0.86rem;
        font-weight: 600;
        color: #6b7280;
        margin-bottom: 6px;
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
        overflow: hidden;
    }
    .metric-progress-inner {
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #4f46e5, #3b82f6);
        transition: width 0.3s ease;
    }
    .metric-percentage {
        font-size: 0.9rem;
        color: #6b7280;
        margin-top: 4px;
    }
    </style>
    """
    st.markdown(card_css, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)

    # ----- CARD 1 -----
    v1 = kpis["tracking_active"]
    with c1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Tracking active</div>
                <div class="metric-value">{v1:,}</div>
                <div class="metric-progress-outer">
                    <div class="metric-progress-inner" style="width: {pct_value(v1):.2f}%;"></div>
                </div>
                <div class="metric-percentage">
                    {pct_str(v1)} of all videos
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ----- CARD 2 -----
    v2 = kpis["completed_age24"]
    with c2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Completed (24h reached)</div>
                <div class="metric-value">{v2:,}</div>
                <div class="metric-progress-outer">
                    <div class="metric-progress-inner" style="width: {pct_value(v2):.2f}%;"></div>
                </div>
                <div class="metric-percentage">
                    {pct_str(v2)} of all videos
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ----- CARD 3 -----
    v3 = kpis["completed_removed"]
    with c3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Removed / Unavailable</div>
                <div class="metric-value">{v3:,}</div>
                <div class="metric-progress-outer">
                    <div class="metric-progress-inner" style="width: {pct_value(v3):.2f}%;"></div>
                </div>
                <div class="metric-percentage">
                    {pct_str(v3)} of all videos
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ----- CARD 4 -----
    v4 = kpis["stopped_low_quality"]
    with c4:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">Stopped (low quality)</div>
                <div class="metric-value">{v4:,}</div>
                <div class="metric-progress-outer">
                    <div class="metric-progress-inner" style="width: {pct_value(v4):.2f}%;"></div>
                </div>
                <div class="metric-percentage">
                    {pct_str(v4)} of all videos
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def compute_system_status(kpis: dict):
    total_videos = kpis.get("total_videos", 0)
    tracking_active = kpis.get("tracking_active", 0)
    completed = kpis.get("completed_total", 0)
    stopped = kpis.get("stopped_total", 0)

    if total_videos == 0:
        return ("Idle", "No videos discovered yet", "#9ca3af")
    if tracking_active > 0:
        return ("Healthy", "Tracking is active and running", "#22c55e")
    if completed + stopped > 0:
        return ("Completed batch", "No active tracking, all videos finished", "#3b82f6")
    return ("Idle", "Waiting for new jobs", "#f59e0b")


# =============== PAGE BODY ===============

st.title("📊 YouTube AutoScanner — Overview")
st.subheader("📌 System KPIs")

ref_col1, ref_col2 = st.columns([1, 3])
with ref_col1:
    if st.button("🔄 Refresh now"):
        load_kpis.clear()
        load_worker_last_runs.clear()
        st.experimental_rerun()

try:
    kpis = load_kpis()
except Exception as e:
    st.error(f"❌ Error loading KPIs: {e}")
    st.stop()

snapshot_ts = kpis.get("snapshot_ts")
if snapshot_ts:
    st.caption(f"Last KPI snapshot: {snapshot_ts}")

top1, top2 = st.columns(2)
top1.metric("Total Videos", f"{kpis['total_videos']:,}")
top2.metric("Total Channels", f"{kpis['total_channels']:,}")

st.markdown("---")

st.markdown("### 🎯 Tracking & Completion Overview")
render_simple_metrics(kpis)

st.markdown("<div style='margin-top: 1.6rem;'></div>", unsafe_allow_html=True)

# === Tracking progress bar ===
st.markdown("### 📈 Tracking Progress")

tracking_total = (
    kpis["tracking_active"] + kpis["completed_total"] + kpis["stopped_total"]
)
finished = kpis["completed_total"] + kpis["stopped_total"]

if tracking_total > 0:
    progress_ratio = finished / tracking_total
    st.progress(
        progress_ratio,
        text=f"{finished:,} / {tracking_total:,} videos finished (~{progress_ratio*100:.1f}%)",
    )
else:
    st.info("No videos in the tracking pipeline yet.")

st.markdown("<div style='margin-top: 1.8rem;'></div>", unsafe_allow_html=True)

# (Outcome summary section đã được bỏ theo yêu cầu)

# === System status & worker activity ===
st.markdown("### 💡 System Status & Worker Activity")

status_label, status_desc, status_color = compute_system_status(kpis)

st.markdown(
    f"""
<div style="
    display:inline-flex;
    align-items:center;
    padding:6px 12px;
    border-radius:999px;
    background:{status_color}1A;
    border:1px solid {status_color};
    margin-bottom:0.7rem;
">
    <span style="width:10px;height:10px;border-radius:999px;background:{status_color};margin-right:8px;"></span>
    <span style="font-weight:600;color:#111827;margin-right:6px;">{status_label}</span>
    <span style="font-size:0.86rem;color:#4b5563;">{status_desc}</span>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("#### Worker last runs")

worker_rows = load_worker_last_runs()
df_workers = pd.DataFrame(worker_rows)
st.dataframe(df_workers, hide_index=True, use_container_width=True)

st.markdown("---")
st.caption("YouTube AutoScanner — Overview Dashboard")
