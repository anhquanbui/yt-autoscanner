import streamlit as st
from datetime import datetime, timezone
import sys
from pathlib import Path

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

# vẽ sidebar dùng chung
render_sidebar_nav()

# ============================================================
# GLOBAL PAGE THEME / CSS
# ============================================================
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {
    background: #f9fafb;
}

.main .block-container {
    max-width: 1100px;
    padding-top: 2.0rem;
    padding-left: 2.2rem;
    padding-right: 2.2rem;
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

.block-container p, .block-container .stMarkdown {
    font-size: 0.95rem;
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# KPI LOADER
# ============================================================
@st.cache_data(ttl=10)
def load_kpis() -> dict:
    """
    Load the most recent KPI snapshot from the `dashboard_kpis` collection.

    Returns
    -------
    dict
        A dictionary with basic tracking counts and viral v2 KPIs.
    """
    db = get_db()
    doc = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    # Default base values if the collection is empty
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
        # Viral v2 KPIs
        "viral2_h6_scored": 0,
        "viral2_h6_candidates": 0,
        "viral2_h12_scored": 0,
        "viral2_12h_viral": 0,
        "viral2_h24_scored": 0,
        "viral2_final_viral": 0,
        "viral2_final_nonviral": 0,
        "viral2_final_nonviral_lowq": 0,
        "viral2_final_unknown": 0,
        "viral2_final_decided": 0,
    }

    if not doc:
        return base

    # Safely coerce numeric fields to int
    for key in base:
        if key == "snapshot_ts":
            continue
        try:
            base[key] = int(doc.get(key, 0))
        except Exception:
            base[key] = 0

    # Format snapshot timestamp (if present)
    ts = doc.get("ts")
    if isinstance(ts, datetime):
        base["snapshot_ts"] = ts.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    elif ts is not None:
        base["snapshot_ts"] = str(ts)

    return base


# ============================================================
# PAGE BODY
# ============================================================
st.title("⚙️ Settings & System Health")

st.caption(
    "Monitor the health of core workers (discover, track, low-quality, ad-friendly, viral, finalize)."
)

st.markdown("---")

# ------------------------------------------------------------
# Refresh button for worker-related cache
# ------------------------------------------------------------
if st.button("🔄 Refresh worker status"):
    load_kpis.clear()
    load_worker_last_runs.clear()
    load_worker_health.clear()
    st.experimental_rerun()

# Load latest KPIs (only needed for the worker health badge)
kpis = load_kpis()

# Show KPI snapshot time (optional)
if kpis.get("snapshot_ts"):
    st.caption(f"Last KPI snapshot: {kpis['snapshot_ts']}")

st.markdown("---")

# ------------------------------------------------------------
# System Status & Worker Activity
# ------------------------------------------------------------
st.markdown("### 💡 System Status & Worker Activity")

render_system_status(kpis)

st.markdown("---")
st.caption("YouTube AutoScanner — Settings, Ad-Friendly Scoring & Worker Monitoring")
