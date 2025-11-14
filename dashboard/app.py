# dashboard/app.py
import streamlit as st

st.set_page_config(
    page_title="YT AutoScanner Dashboard",
    page_icon="📊",
    layout="wide",
)

# ========== SIDEBAR ==========

with st.sidebar:
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
    <div style="font-size:0.8rem; color:#6b7280;">Internal tracking & ML dashboard</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    st.markdown("#### 🧭 Navigation tips")
    st.markdown(
        """
- **Overview** – high-level KPIs & trends  
- **Videos** – per-video stats & timelines  
- **Channels** – aggregated channel performance  
- **ML Models** – low-quality / viral model insights  
- **Logs & System** – workers & health signals  
"""
    )

    st.markdown("---")
    st.markdown(
        """
<small style="color:#9ca3af;">
YouTube AutoScanner runs a 24h window to track early performance and keep your dataset clean with ML-based low-quality filters.
</small>
        """,
        unsafe_allow_html=True,
    )

# ========== MAIN CONTENT (HOME) ==========

# Hero banner
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
    Track fresh videos, clean your dataset, boost your models.
  </div>
  <div style="font-size:0.95rem; max-width:720px; line-height:1.5;">
    This dashboard gives you a quick view of how the scanner is discovering and tracking videos
    in their first 24 hours, how many are being completed naturally, and how many are stopped
    early by the low-quality ML filter.
  </div>
</div>
    """,
    unsafe_allow_html=True,
)

# Section title
st.markdown("### 🧩 What you can do here")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(
        """
<div style="
    border-radius:14px;
    padding:1rem 1rem 0.9rem 1rem;
    border:1px solid #e5e7eb;
    background: #ffffff;
    box-shadow: 0 6px 18px rgba(15,23,42,0.06);
">
  <div style="font-size:1.2rem; margin-bottom:0.4rem;">📈 Overview</div>
  <div style="font-size:0.9rem; color:#4b5563; margin-bottom:0.3rem;">
    See total videos, channels, tracking vs. completed vs. low-quality stopped, and
    how many new videos are discovered each day.
  </div>
  <div style="font-size:0.8rem; color:#6b7280;">
    Start here to check if the pipeline is healthy and tracking as expected.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

with col2:
    st.markdown(
        """
<div style="
    border-radius:14px;
    padding:1rem 1rem 0.9rem 1rem;
    border:1px solid #e5e7eb;
    background: #ffffff;
    box-shadow: 0 6px 18px rgba(15,23,42,0.06);
">
  <div style="font-size:1.2rem; margin-bottom:0.4rem;">🎥 Videos & Channels</div>
  <div style="font-size:0.9rem; color:#4b5563; margin-bottom:0.3rem;">
    Drill down into individual videos, timelines of views, and which channels are
    contributing the most to your dataset.
  </div>
  <div style="font-size:0.8rem; color:#6b7280;">
    Useful when you want to inspect outliers or debug weird tracking behaviour.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

with col3:
    st.markdown(
        """
<div style="
    border-radius:14px;
    padding:1rem 1rem 0.9rem 1rem;
    border:1px solid #e5e7eb;
    background: #ffffff;
    box-shadow: 0 6px 18px rgba(15,23,42,0.06);
">
  <div style="font-size:1.2rem; margin-bottom:0.4rem;">🤖 ML & System</div>
  <div style="font-size:0.9rem; color:#4b5563; margin-bottom:0.3rem;">
    Review low-quality filtering, model performance snapshots, and worker logs to
    understand how the scanner behaves in production.
  </div>
  <div style="font-size:0.8rem; color:#6b7280;">
    Ideal for tuning thresholds and verifying that your models are doing the right thing.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")

st.markdown(
    """
<small style="color:#9ca3af;">
Tip: use the <b>Overview</b> page first to confirm the pipeline is running, then move to
<b>Videos</b> or <b>ML Models</b> when you need more detail.
</small>
""",
    unsafe_allow_html=True,
)
