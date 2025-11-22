import streamlit as st
import pandas as pd
from datetime import datetime, timezone

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # .../yt-autoscanner
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

    for key in base:
        if key == "snapshot_ts":
            continue
        try:
            base[key] = int(doc.get(key, 0))
        except:
            base[key] = 0

    ts = doc.get("ts")
    if isinstance(ts, datetime):
        ts = ts.astimezone(timezone.utc)
        base["snapshot_ts"] = ts.strftime("%Y-%m-%d %H:%M UTC")
    elif ts:
        base["snapshot_ts"] = str(ts)

    return base


@st.cache_data(ttl=60)
def load_worker_last_runs():
    db = get_db()

    workers = [
        ("discover_once", "Discover new videos"),
        ("track_once", "Track stats & snapshots"),
        ("low_quality_autoflag_3h", "Low-quality scoring (3h)"),
        ("low_quality_autoflag_6h", "Low-quality scoring (6h)"),
        ("compute_dashboard_kpis", "Dashboard KPI snapshot"),
    ]

    now = datetime.now(timezone.utc)
    rows = []

    for key, label in workers:
        doc = db.worker_runs.find_one({"name": key}, sort=[("last_run", -1)])

        if not doc or "last_run" not in doc:
            rows.append({"Worker": label, "Last run": "No data"})
            continue

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
                pretty = f"{int(sec // 86400)}d ago"
        else:
            pretty = str(ts)

        rows.append({"Worker": label, "Last run": pretty})

    return rows


@st.cache_data(ttl=60)
def load_worker_health():
    """
    Determine worker health:
    - Healthy: status=ok AND last_run < 3h old
    - Warning: some worker stale or error
    - Stopped: no record for worker
    """
    db = get_db()

    core_workers = [
        "discover_once",
        "track_once",
        "low_quality_autoflag_3h",
        "low_quality_autoflag_6h",
        "compute_dashboard_kpis",
    ]

    now = datetime.now(timezone.utc)
    stale_sec = 3 * 3600  # >3h considered stale

    summary = {"total": len(core_workers), "healthy": 0, "warning": 0, "stopped": 0}

    for w in core_workers:
        doc = db.worker_runs.find_one({"name": w}, sort=[("last_run", -1)])

        if not doc or "last_run" not in doc:
            summary["stopped"] += 1
            continue

        ts = doc["last_run"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
        else:
            age = 99999999

        status_raw = str(doc.get("status", "")).lower()

        has_error = (
            status_raw.startswith("err")
            or status_raw in {"error", "failed", "fatal", "stopped"}
        )
        is_stale = age > stale_sec

        if has_error or is_stale:
            summary["warning"] += 1
        else:
            summary["healthy"] += 1

    return summary


# =============== METRIC CARDS ===============

def render_simple_metrics(kpis: dict):
    total_videos = kpis.get("total_videos", 0)

    def pct_value(v):
        return (v / total_videos * 100.0) if total_videos else 0.0

    def pct_str(v):
        return f"{pct_value(v):.2f}%"

    st.markdown(
        """
<style>
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
</style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)

    cards = [
        ("Tracking active", kpis["tracking_active"]),
        ("Completed (24h reached)", kpis["completed_age24"]),
        ("Removed / Unavailable", kpis["completed_removed"]),
        ("Stopped (low quality)", kpis["stopped_low_quality"]),
    ]

    for col, (title, value) in zip([c1, c2, c3, c4], cards):
        col.markdown(
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


# =============== SYSTEM STATUS ===============

def compute_system_status(kpis: dict, wh: dict):
    total_videos = kpis.get("total_videos", 0)

    if total_videos == 0:
        return ("Idle", "No videos discovered yet", "#9ca3af")

    healthy = wh["healthy"]
    warning = wh["warning"]
    stopped = wh["stopped"]
    total = wh["total"]

    if healthy == 0 and (warning + stopped) > 0:
        return ("Stopped", "All workers appear stopped or stale", "#ef4444")

    if (warning + stopped) > 0:
        return ("Warning", f"{warning + stopped} worker(s) have issues", "#facc15")

    return ("Healthy", "All core workers running normally", "#22c55e")



# =============== PAGE BODY ===============

st.title("📊 YouTube AutoScanner — Overview")
st.subheader("📌 System KPIs")

if st.button("🔄 Refresh now"):
    load_kpis.clear()
    load_worker_last_runs.clear()
    load_worker_health.clear()
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

st.markdown("<div style='margin-top:1.4rem'></div>", unsafe_allow_html=True)

# === Tracking progress bar ===
st.markdown("### 📈 Tracking Progress")
tracking_total = kpis["tracking_active"] + kpis["completed_total"] + kpis["stopped_total"]
finished = kpis["completed_total"] + kpis["stopped_total"]

if tracking_total > 0:
    ratio = finished / tracking_total
    st.progress(ratio, text=f"{finished:,} / {tracking_total:,} videos (~{ratio*100:.1f}%)")
else:
    st.info("No videos in tracking pipeline.")

st.markdown("<div style='margin-top:1.8rem'></div>", unsafe_allow_html=True)


# === System status & worker activity ===
st.markdown("### 💡 System Status & Worker Activity")

worker_rows = load_worker_last_runs()
worker_health = load_worker_health()

label, desc, color = compute_system_status(kpis, worker_health)

st.markdown(
    f"""
<div style="
  display:flex;align-items:center;
  padding:6px 12px;border-radius:999px;
  background:{color}1A;border:1px solid {color};
  margin-bottom:0.7rem;">
  <span style="width:10px;height:10px;border-radius:999px;background:{color};margin-right:8px;"></span>
  <span style="font-weight:600;color:#111827;margin-right:6px;">{label}</span>
  <span style="font-size:0.86rem;color:#4b5563;">{desc}</span>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("#### Worker last runs")
st.dataframe(pd.DataFrame(worker_rows), hide_index=True, use_container_width=True)

st.markdown("---")
st.caption("YouTube AutoScanner — Overview Dashboard")
