# dashboard/app.py

import os
import platform
import socket
from datetime import datetime

import streamlit as st

from components.db import get_client  # hoặc get_db nếu bạn cần sau này


# ==========================
# ⚙ PAGE CONFIG
# ==========================
st.set_page_config(
    page_title="YT AutoScanner Dashboard",
    page_icon="📊",
    layout="wide",
)


# ==========================
# 🔧 VPS SYSTEM STATUS
# ==========================
def get_system_info():
    return {
        "hostname": socket.gethostname(),
        "os": platform.system() + " " + platform.release(),
        "python": platform.python_version(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_mongo_status():
    try:
        client = get_client()
        client.admin.command("ping")
        return "🟢 Connected"
    except Exception:
        return "🔴 Not connected"


# ==========================
# 🧭 SIDEBAR
# ==========================
with st.sidebar:
    st.markdown(
        """
<div style='display:flex;gap:0.7rem;align-items:center;margin-bottom:1rem;'>
    <div style='
        width:36px;height:36px;border-radius:50%;
        background:linear-gradient(135deg,#ec4899,#8b5cf6);
        display:flex;align-items:center;justify-content:center;
        font-weight:700;color:white;font-size:17px;'
    >YT</div>
    <div>
        <div style='font-size:1rem;font-weight:700;'>YT AutoScanner</div>
        <div style='font-size:0.82rem;color:#6b7280;'>
            VPS Monitoring &amp; ML Pipeline
        </div>
    </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")

    st.markdown("### 📌 Navigation")
    choice = st.radio(
        "",
        [
            "🏠 Home",
            "📈 Overview",
            # sau này thêm:
            # "🎥 Videos",
            # "📊 Channels",
            # "🤖 ML Models",
            # "🛠 System & Logs",
        ],
        label_visibility="collapsed",
    )

    st.markdown("---")

    info = get_system_info()
    st.markdown("### 🖥 VPS Status")

    st.markdown(
        f"""
- **Hostname:** `{info['hostname']}`
- **OS:** `{info['os']}`
- **Python:** `{info['python']}`
- **Local Time:** `{info['time']}`
- **MongoDB:** {get_mongo_status()}
"""
    )

    st.markdown("---")

    st.markdown(
        """
<small style="color:#9ca3af;">
Running on VPS • Powered by systemd workers •  
Auto-discovery, auto-tracking, and ML pipeline included.
</small>
        """,
        unsafe_allow_html=True,
    )


# ==========================
# 🏠 MAIN CONTENT
# ==========================
if choice == "🏠 Home":
    st.markdown(
        """
<div style="
    padding: 1.6rem 1.8rem;
    border-radius: 20px;
    background: radial-gradient(circle at top left, #0ea5e9, #6366f1 55%, #111827 100%);
    color: white;
    margin-bottom: 1.7rem;
    box-shadow: 0 20px 35px rgba(0,0,0,0.35);
">
  <div style="font-size:0.9rem; opacity:0.85; margin-bottom:0.3rem;">
    YouTube AutoScanner • VPS Deployment
  </div>

  <div style="font-size:1.9rem; font-weight:700; margin-bottom:0.35rem;">
    Your central hub for monitoring the 24h tracking pipeline.
  </div>

  <div style="font-size:1rem; max-width:740px; line-height:1.55;">
    This dashboard gives you real-time visibility into discovery speed,
    tracking health, ML filtering behaviour, system logs, and MongoDB dataset intelligence.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### 🚀 What you can do here")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.info("📈 **Overview**\n\nKPIs, completion rates, low-quality filter impact.")
    with col2:
        st.success("🎥 **Data Export & BI**\n\nUse Mongo → Parquet to power ML & dashboards.")
    with col3:
        st.warning("🤖 **ML Filters**\n\nMonitor how 3h/6h models prune low-quality videos.")

    st.markdown("---")
    st.markdown(
        "<small style='color:#9ca3af;'>Use the sidebar to switch to the Overview.</small>",
        unsafe_allow_html=True,
    )

elif choice == "📈 Overview":
    # Multi-page: nhảy sang dashboard/pages/01_Overview.py
    # Đường dẫn phải là path tương đối từ thư mục chứa app.py (dashboard)
    st.switch_page("pages/01_Overview.py")
