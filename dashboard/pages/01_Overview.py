# dashboard/pages/01_Overview.py

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timezone

from components.db import get_db


# =============== GLOBAL LAYOUT FIX ===============
# Keep the main container centered & stable across refreshes
st.markdown(
    """
<style>
.main .block-container {
    max-width: 1200px;
    padding-top: 2rem;
    padding-left: 2.5rem;
    padding-right: 2.5rem;
    margin-left: auto;
    margin-right: auto;
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

    This assumes a background worker (compute_dashboard_kpis)
    periodically aggregates KPIs from `videos` and inserts docs like:

      {
        total_videos,
        total_channels,
        tracking_active,
        completed_total,
        stopped_total,
        completed_age24,
        completed_removed,
        stopped_low_quality,
        low_quality_flagged,
        ts: <datetime>
      }
    """
    db = get_db()
    doc = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    # Base structure with safe defaults
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

    # Copy numeric fields defensively
    for key in [
        "total_videos",
        "total_channels",
        "tracking_active",
        "completed_total",
        "stopped_total",
        "completed_age24",
        "completed_removed",
        "stopped_low_quality",
        "low_quality_flagged",
    ]:
        value = doc.get(key, 0)
        try:
            base[key] = int(value)
        except Exception:
            base[key] = 0

    # Snapshot timestamp (string for display)
    ts = doc.get("ts")
    if isinstance(ts, datetime):
        # Normalize to UTC string for display
        ts = ts.astimezone(timezone.utc)
        base["snapshot_ts"] = ts.strftime("%Y-%m-%d %H:%M UTC")
    elif ts is not None:
        base["snapshot_ts"] = str(ts)

    return base


@st.cache_data(ttl=60)
def load_worker_last_runs():
    """
    Read latest run timestamps for each worker from `worker_runs`
    and return humanized "Last run" strings with friendly names.
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
                # Ensure ts is timezone-aware (assume UTC if naive)
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
                # Fallback if last_run is stored as string or other type
                pretty = str(ts)
        else:
            pretty = "No data"

        results.append({"Worker": label, "Last run": pretty})

    return results


# =============== STYLE RENDERERS ===============

def render_style_1(kpis: dict):
    """Simple metrics using st.metric."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tracking active", f"{kpis['tracking_active']:,}")
    c2.metric("Completed (24h reached)", f"{kpis['completed_age24']:,}")
    c3.metric("Removed / Unavailable", f"{kpis['completed_removed']:,}")
    c4.metric("Stopped (low quality)", f"{kpis['stopped_low_quality']:,}")


def render_style_2(kpis: dict):
    """Gradient cards style."""
    st.markdown(
        """
<style>
.grad-card {
    border-radius: 14px;
    padding: 16px 18px;
    color: #ffffff;
    box-shadow: 0 10px 25px rgba(15, 23, 42, 0.25);
}
.grad-1 {
    background: linear-gradient(135deg, #2563eb, #22c55e);
}
.grad-2 {
    background: linear-gradient(135deg, #22c55e, #facc15);
}
.grad-3 {
    background: linear-gradient(135deg, #f97316, #facc15);
}
.grad-4 {
    background: linear-gradient(135deg, #ef4444, #ec4899);
}
.grad-title {
    font-size: 0.9rem;
    font-weight: 600;
    opacity: 0.9;
}
.grad-value {
    margin-top: 6px;
    font-size: 2.3rem;
    font-weight: 650;
}
.grad-sub {
    font-size: 0.8rem;
    opacity: 0.9;
    margin-top: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            f"""
            <div class="grad-card grad-1">
                <div class="grad-title">Tracking Active</div>
                <div class="grad-value">{kpis['tracking_active']:,}</div>
                <div class="grad-sub">Videos currently being polled</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="grad-card grad-2">
                <div class="grad-title">Completed (24h reached)</div>
                <div class="grad-value">{kpis['completed_age24']:,}</div>
                <div class="grad-sub">Finished their 24h cycle</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="grad-card grad-3">
                <div class="grad-title">Removed / Unavailable</div>
                <div class="grad-value">{kpis['completed_removed']:,}</div>
                <div class="grad-sub">Video removed/deleted/not found</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""
            <div class="grad-card grad-4">
                <div class="grad-title">Stopped (Low Quality)</div>
                <div class="grad-value">{kpis['stopped_low_quality']:,}</div>
                <div class="grad-sub">Filtered early by ML</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_style_3_glass(kpis: dict):
    """Glassmorphism cards (Theme Style 3)."""
    st.markdown(
        """
<style>
.glass-card {
    background: rgba(255, 255, 255, 0.12);
    border-radius: 18px;
    padding: 16px 20px;
    border: 1px solid rgba(255,255,255,0.4);
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.18);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
}
.glass-title {
    font-weight: 600;
    font-size: 0.95rem;
    color: #111827;
    margin-bottom: 4px;
}
.glass-value {
    font-size: 2.3rem;
    font-weight: 650;
    margin: 0;
    color: #111827;
}
.glass-sub {
    font-size: 0.8rem;
    color: #6b7280;
    margin-top: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">🔄 Tracking Active</div>
                <p class="glass-value">{kpis['tracking_active']:,}</p>
                <div class="glass-sub">Videos currently being polled</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">✅ Completed (24h reached)</div>
                <p class="glass-value">{kpis['completed_age24']:,}</p>
                <div class="glass-sub">Finished their 24h tracking lifecycle</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">📤 Removed / Unavailable</div>
                <p class="glass-value">{kpis['completed_removed']:,}</p>
                <div class="glass-sub">Removed, deleted or not found</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">⛔ Stopped (Low Quality)</div>
                <p class="glass-value">{kpis['stopped_low_quality']:,}</p>
                <div class="glass-sub">Stopped early by low-quality filter</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def compute_system_status(kpis: dict):
    """Return (label, description, color_hex) for system status pill."""
    total_videos = kpis.get("total_videos", 0)
    tracking_active = kpis.get("tracking_active", 0)
    completed = kpis.get("completed_total", 0)
    stopped = kpis.get("stopped_total", 0)

    if total_videos == 0:
        return ("Idle", "No videos discovered yet", "#9ca3af")  # gray
    if tracking_active > 0:
        return ("Healthy", "Tracking is active and running", "#22c55e")  # green
    if completed + stopped > 0:
        return ("Completed batch", "No active tracking, all videos finished", "#3b82f6")  # blue
    return ("Idle", "Waiting for new jobs", "#f59e0b")  # amber


# =============== PAGE BODY ===============

st.title("📊 YouTube AutoScanner — Overview")
st.subheader("📌 System KPIs")

# Refresh button
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

# Show snapshot timestamp if available
snapshot_ts = kpis.get("snapshot_ts")
if snapshot_ts:
    st.caption(f"Last KPI snapshot: {snapshot_ts}")

# Top-level basic KPIs
top1, top2 = st.columns(2)
top1.metric("Total Videos", f"{kpis['total_videos']:,}")
top2.metric("Total Channels", f"{kpis['total_channels']:,}")

st.markdown("---")

# === Overview page settings in sidebar ===
with st.sidebar:
    st.markdown("#### ⚙️ Overview page settings")
    style_choice = st.radio(
        "Theme style",
        ["Theme Style 1", "Theme Style 2", "Theme Style 3"],
        index=2,  # default: Theme Style 3 (Glass)
    )

st.markdown("### 🎯 Tracking & Completion Overview")

if style_choice.startswith("Theme Style 1"):
    render_style_1(kpis)
elif style_choice.startswith("Theme Style 2"):
    render_style_2(kpis)
else:
    render_style_3_glass(kpis)

st.markdown("<div style='margin-top: 1.5rem;'></div>", unsafe_allow_html=True)

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

st.markdown("<div style='margin-top: 1.5rem;'></div>", unsafe_allow_html=True)

# === Outcome breakdown (donut chart) ===
st.markdown("### 🥧 Outcome Breakdown (24h window)")

outcome_df_full = pd.DataFrame(
    {
        "Reason": [
            "Natural (24h reached)",
            "Removed / Unavailable",
            "Stopped early (Low quality)",
        ],
        "Count": [
            kpis["completed_age24"],
            kpis["completed_removed"],
            kpis["stopped_low_quality"],
        ],
    }
)

outcome_df_pie = outcome_df_full[outcome_df_full["Count"] > 0]
outcome_total = (
    int(outcome_df_full["Count"].sum()) if not outcome_df_full.empty else 0
)

if outcome_total == 0 or outcome_df_pie.empty:
    st.info("No completed or stopped videos yet — keep tracking to see the breakdown.")
else:
    # Pie chart theme based on selected Theme Style
    if style_choice.startswith("Theme Style 1"):
        template = "plotly_white"
        colors = px.colors.sequential.Blues
    elif style_choice.startswith("Theme Style 2"):
        template = "plotly_white"
        colors = ["#2563eb", "#22c55e", "#facc15", "#ef4444"]
    else:  # Theme Style 3 (Glass) – soft pastels
        template = "plotly_white"
        colors = px.colors.qualitative.Pastel

    fig_completed = px.pie(
        outcome_df_pie,
        names="Reason",
        values="Count",
        hole=0.4,
        title="Distribution of outcomes by stop reason",
        color_discrete_sequence=colors,
    )
    fig_completed.update_layout(template=template)
    fig_completed.update_traces(
        textinfo="value+percent",
        hovertemplate="%{label}<br>%{value} videos<br>%{percent}",
    )
    st.plotly_chart(fig_completed, use_container_width=True)

# Outcome summary (always show all three reasons)
st.markdown("#### Outcome summary")
for _, row in outcome_df_full.iterrows():
    st.markdown(f"- **{row['Reason']}**: `{int(row['Count']):,}` video(s)")

st.markdown("---")

# === Low-quality filter impact ===
st.markdown("### 🧠 Low-quality Filter Impact")

finished_videos = kpis["completed_total"] + kpis["stopped_total"]
pct_low_of_finished = (
    (kpis["stopped_low_quality"] / finished_videos * 100.0)
    if finished_videos > 0
    else 0.0
)
pct_flagged_of_all = (
    (kpis["low_quality_flagged"] / kpis["total_videos"] * 100.0)
    if kpis["total_videos"] > 0
    else 0.0
)

c1, c2, c3 = st.columns(3)
c1.metric("Stopped early (low quality)", f"{kpis['stopped_low_quality']:,}")
c2.metric("% of finished videos", f"{pct_low_of_finished:.1f}%")
c3.metric("% of all videos flagged", f"{pct_flagged_of_all:.1f}%")

st.markdown("---")

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
    margin-bottom:0.3rem;
">
    <span style="width:10px;height:10px;border-radius:999px;background:{status_color};margin-right:8px;"></span>
    <span style="font-weight:600;color:#111827;margin-right:6px;">{status_label}</span>
    <span style="font-size:0.85rem;color:#4b5563;">{status_desc}</span>
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
