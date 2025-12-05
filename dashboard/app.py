# ============================
# Bootstrap Python path (add project root so `config` is importable)
# ============================
import sys
from pathlib import Path

# We assume this file lives under:
#   <project_root>/dashboard/app.py (or 00_Home.py)
# so `parents[1]` should be the project root itself.
ROOT = Path(__file__).resolve().parents[1]  # .../yt-autoscanner
if str(ROOT) not in sys.path:
    # Prepend project root to sys.path so imports like `config.*` work
    # even when Streamlit runs from a different working directory.
    sys.path.insert(0, str(ROOT))

# ============================
# Streamlit app
# ============================
import streamlit as st

# Global page configuration for this app tab.
st.set_page_config(
    page_title="YT AutoScanner Dashboard",
    page_icon="📊",
    layout="wide",
)

# ========== SIDEBAR ==========

with st.sidebar:
    # Brand / header block
    st.markdown(
        """
<div style="
    display:flex;
    flex-direction:row;
    align-items:center;
    gap:0.6rem;
    margin-bottom:0.75rem;
">
  <div style="
      width:32px;
      height:32px;
      border-radius:999px;
      display:flex;
      align-items:center;
      justify-content:center;
      background:linear-gradient(135deg,#ec4899,#8b5cf6);
      color:white;
      font-size:18px;
      font-weight:700;
  ">
    YT
  </div>
  <div>
    <div style="font-weight:700; font-size:0.95rem;">YT AutoScanner</div>
    <div style="font-size:0.8rem; color:#6b7280;">
      24h tracking · ML virality · Ad-friendly
    </div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    # Quick navigation hints tailored to your current pages
    st.markdown("#### 🧭 Navigation map")

    st.markdown(
        """
- **Overview** – high-level KPIs, tracking status, worker health  
- **Viral Filter** – final viral decisions (weak / viral / super) + behavior tags  
- **Low-Quality Filter** – 3h / 6h low-quality ML blocks for noisy videos  
- **Ad-Friendly** – teacher-model labels & rule-based ad suitability  
- **Videos / Channels** – drill-down for individual videos & channels  
- **Settings / Workers** – worker status
"""
    )

    st.markdown("---")

# ========== MAIN CONTENT (HOME) ==========

# Hero banner: explain the full pipeline
st.markdown(
    """
<div style="
    padding: 1.5rem 1.8rem;
    border-radius: 18px;
    background: radial-gradient(circle at top left, #0ea5e9, #6366f1 55%, #111827 100%);
    color: white;
    margin-bottom: 1.5rem;
    box-shadow: 0 18px 35px rgba(15,23,42,0.4);
">
  <div style="font-size:0.9rem; opacity:0.9; margin-bottom:0.25rem;">
    Internal dashboard • YouTube AutoScanner
  </div>
  <div style="font-size:1.7rem; font-weight:700; margin-bottom:0.35rem;">
    End-to-end 24h pipeline: discover, track, score, finalize.
  </div>
  <div style="font-size:0.95rem; max-width:780px; line-height:1.5;">
    The scanner discovers fresh YouTube videos, tracks their first 24 hours,
    applies low-quality filters at 3h/6h, multi-stage virality models at 6h/12h/24h
    (with <b>weak / viral / super</b> labels and behavior tags), and an ad-friendly
    teacher model. Final decisions are saved back to Mongo so you can analyze,
    explain, and iterate on your models.
  </div>

  <div style="
      display:flex;
      flex-wrap:wrap;
      gap:0.4rem;
      margin-top:0.9rem;
      font-size:0.78rem;
  ">
    <span style="
        padding:0.25rem 0.55rem;
        border-radius:999px;
        background:rgba(15,23,42,0.18);
        border:1px solid rgba(209,250,229,0.5);
    ">
      Discover → Track → 24h lifecycle
    </span>
    <span style="
        padding:0.25rem 0.55rem;
        border-radius:999px;
        background:rgba(15,23,42,0.18);
        border:1px solid rgba(191,219,254,0.6);
    ">
      Low-quality ML (3h / 6h)
    </span>
    <span style="
        padding:0.25rem 0.55rem;
        border-radius:999px;
        background:rgba(15,23,42,0.18);
        border:1px solid rgba(254,215,170,0.7);
    ">
      Viral v2 (6h / 12h / 24h + behavior)
    </span>
    <span style="
        padding:0.25rem 0.55rem;
        border-radius:999px;
        background:rgba(15,23,42,0.18);
        border:1px solid rgba(252,231,243,0.6);
    ">
      Ad-friendly teacher + rules
    </span>
    <span style="
        padding:0.25rem 0.55rem;
        border-radius:999px;
        background:rgba(15,23,42,0.18);
        border:1px solid rgba(209,213,219,0.7);
    ">
      Final dataset for research & dashboards
    </span>
  </div>
</div>
    """,
    unsafe_allow_html=True,
)

# Section title for feature cards
st.markdown("### 🧩 How to use this dashboard")

# Three main "entry points"
col1, col2, col3 = st.columns(3)

with col1:
    # Card 1: Pipeline & health (Overview)
    st.markdown(
        """
<div style="
    border-radius:14px;
    padding:1rem 1rem 0.9rem 1rem;
    border:1px solid #e5e7eb;
    background:#ffffff;
    box-shadow:0 6px 18px rgba(15,23,42,0.06);
">
  <div style="font-size:1.2rem; margin-bottom:0.4rem;">📈 Overview & Health</div>
  <div style="font-size:0.9rem; color:#4b5563; margin-bottom:0.3rem;">
    Check how many videos are being discovered and tracked in the last 24h,
    how many complete naturally, and how many are blocked by low-quality rules.
    See quick worker health and basic KPIs.
  </div>
  <div style="font-size:0.8rem; color:#6b7280;">
    Start here when you want to know if the pipeline is running smoothly
    or if something looks stuck.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

with col2:
    # Card 2: Viral & behavior explanations
    st.markdown(
        """
<div style="
    border-radius:14px;
    padding:1rem 1rem 0.9rem 1rem;
    border:1px solid #e5e7eb;
    background:#ffffff;
    box-shadow:0 6px 18px rgba(15,23,42,0.06);
">
  <div style="font-size:1.2rem; margin-bottom:0.4rem;">🚀 Virality & Behavior</div>
  <div style="font-size:0.9rem; color:#4b5563; margin-bottom:0.3rem;">
    Use the <b>Viral Filter</b> page to inspect final viral decisions from the
    6h/12h/24h models. Filter by keyword, region, and final status
    (weak / viral / super), and read the <b>Behavior</b> tag to see whether a video
    is early-peak, late-growth, consistent, or volatile.
  </div>
  <div style="font-size:0.8rem; color:#6b7280;">
    Ideal for explaining why a low-view video is still flagged as viral,
    or why some candidates are downgraded after 24h.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

with col3:
    # Card 3: Quality & ad-friendly + system controls
    st.markdown(
        """
<div style="
    border-radius:14px;
    padding:1rem 1rem 0.9rem 1rem;
    border:1px solid #e5e7eb;
    background:#ffffff;
    box-shadow:0 6px 18px rgba(15,23,42,0.06);
">
  <div style="font-size:1.2rem; margin-bottom:0.4rem;">🛡️ Quality & System</div>
  <div style="font-size:0.9rem; color:#4b5563; margin-bottom:0.3rem;">
    Review <b>low-quality</b> blocks and <b>ad-friendly</b> decisions, then jump to
    <b>Settings / Workers</b> to see which workers are running and how often they
    write to <code>worker_runs</code>. Use Logs/System pages when you need raw
    error messages or more debugging detail.
  </div>
  <div style="font-size:0.8rem; color:#6b7280;">
    Use this when tuning thresholds, changing model versions, or checking if the
    teacher model and rules are behaving as expected.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

# Horizontal separator
st.markdown("---")

# Final hint about recommended flow
st.markdown(
    """
<small style="color:#9ca3af;">
Typical flow: start with <b>Overview</b> to confirm the pipeline and workers,
then use <b>Viral Filter</b> and <b>Ad-Friendly</b> to inspect model decisions.
Jump into <b>Videos / Channels</b> when you spot something that needs
per-video investigation.
</small>
""",
    unsafe_allow_html=True,
)
