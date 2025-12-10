# ============================
# Bootstrap Python path
# ============================
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent          # .../Internal/dashboard
INTERNAL_ROOT = APP_DIR.parents[0]                 # .../Internal
PROJECT_ROOT = APP_DIR.parents[1]                  # .../yt-autoscanner

for p in (PROJECT_ROOT, INTERNAL_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import streamlit as st

# ============================
# Streamlit page config
# ============================
st.set_page_config(
    page_title="YT AutoScanner Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================
# Sidebar (import shared menu)
# ============================
from dashboard.components.sidebar_nav import render_sidebar_nav
render_sidebar_nav()

# ============================
# MAIN PAGE CONTENT
# ============================

# ----- Hero Section -----
st.markdown(
    """
    <style>
    .hero-box {
        background: linear-gradient(95deg, #3b82f6, #2563eb);
        padding: 30px 35px;
        border-radius: 14px;
        color: white;
        margin-bottom: 25px;
    }
    .hero-title {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 6px;
    }
    .hero-sub {
        font-size: 0.97rem;
        opacity: 0.92;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero-box">
        <div class="hero-title">End-to-end 24h pipeline: discover, track, score & decide</div>
        <div class="hero-sub">
            Internal dashboard • YouTube AutoScanner<br>
            The scanner discovers fresh YouTube videos, tracks their first 24h lifecycle and applies ML virality models at 6h/12h/24h 
            (weak / viral / super viral). Decisions persist back to MongoDB.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ----- How to use section -----
st.markdown("## 🧩 How to use this dashboard")
st.info(
    """
- Start with **Overview (Home)** → See pipeline status & if workers are healthy  
- Go to **System KPIs** → High-level tracking, viral breakdown  
- Open **Viral Filter** → Final decisions + behaviors  
- Check **Settings & Workers** → Restart, refresh worker health  
"""
)

st.caption("Typical usage: Overview → KPIs → Filter → Worker health")
