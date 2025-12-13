import streamlit as st
from datetime import datetime, timezone
import sys
from pathlib import Path

# Ensure project imports work
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
# Page CSS
# =========================
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

# =========================
# KPI loader
# =========================
@st.cache_data(ttl=10)
def load_kpis() -> dict:
    """Load latest dashboard_kpis snapshot."""
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

    for key in base:
        if key == "snapshot_ts":
            continue
        try:
            base[key] = int(doc.get(key, 0))
        except Exception:
            base[key] = 0

    ts = doc.get("ts")
    if isinstance(ts, datetime):
        base["snapshot_ts"] = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    elif ts is not None:
        base["snapshot_ts"] = str(ts)

    return base


# =========================
# Page body
# =========================
st.title("⚙️ Settings & System Health")
st.caption("Monitor core worker health (discover, track, low-quality, ad-friendly, viral, finalize).")

st.markdown("---")

if st.button("🔄 Refresh worker status"):
    load_kpis.clear()
    load_worker_last_runs.clear()
    load_worker_health.clear()
    st.experimental_rerun()

kpis = load_kpis()

if kpis.get("snapshot_ts"):
    st.caption(f"Last KPI snapshot: {kpis['snapshot_ts']}")

st.markdown("---")

st.markdown("### 💡 System Status & Worker Activity")
render_system_status(kpis)

st.markdown("---")
st.caption("YouTube AutoScanner — Settings, Ad-Friendly Scoring & Worker Monitoring")
