#!/usr/bin/env python3
"""
Streamlit page: 02_Videos (minimal KPI view)

- Total Videos / Channels / Tracking Active
- Tracking Status (Completed breakdown + Stopped low-quality)
- Low-quality ML Models (3h / 6h) — flagged counts
- Latest 200 videos (video_id, title, channel)
"""

from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st
import pandas as pd

from config.db import get_db


# ======================================================
# Page config
# ======================================================

st.set_page_config(page_title="Videos Overview", layout="wide")


# ======================================================
# DB init
# ======================================================

db = get_db()
videos_col = db.videos
kpis_col = db.dashboard_kpis


# ======================================================
# Helpers
# ======================================================

def pct(num: float, den: float) -> float:
    """Safe percentage helper."""
    try:
        if not den:
            return 0.0
        return round((num / den) * 100, 2)
    except Exception:
        return 0.0


def stat_card(
    title: str,
    value: int | float,
    base: int | float,
    base_label: str,
    icon: str = "",
) -> None:
    """Render a card with value + mini progress bar."""
    percentage = pct(value, base)

    bar_html = f"""
        <div style="background:#eee; height:6px; width:100%; border-radius:4px; margin-top:4px;">
            <div style="
                background:#ff6600;
                height:6px;
                width:{percentage}%;
                border-radius:4px;
                transition: width 0.4s ease-out;
            "></div>
        </div>
    """

    st.markdown(
        f"""
        <div style="
            padding:16px 18px;
            border-radius:16px;
            background:#ffffff;
            border:1px solid #f0f0f0;
            box-shadow:0 2px 8px rgba(0,0,0,0.04);
        ">
            <div style="font-size:18px; font-weight:600; margin-bottom:4px;">
                {icon} {title}
            </div>
            <div style="font-size:26px; font-weight:800; margin-bottom:0px;">
                {value:,}
            </div>
            <div style="font-size:13px; color:#666; margin-top:-2px;">
                {percentage}% of {base_label}
            </div>
            {bar_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=30)
def get_latest_kpis() -> Dict[str, Any] | None:
    """Latest snapshot from dashboard_kpis."""
    return kpis_col.find_one(sort=[("_id", -1)])


@st.cache_data(ttl=30)
def get_recent_videos(limit: int = 200) -> List[Dict[str, Any]]:
    """Latest N videos for table (id, title, channel)."""
    cursor = (
        videos_col.find(
            {},
            {
                "_id": 1,
                "snippet.title": 1,
                "snippet.channelTitle": 1,
            },
        )
        .sort("published_at", -1)
        .limit(limit)
    )

    rows: List[Dict[str, Any]] = []
    for doc in cursor:
        snippet = doc.get("snippet", {}) or {}
        rows.append(
            {
                "video_id": doc.get("_id"),
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
            }
        )

    return rows


# ======================================================
# Page layout
# ======================================================

k = get_latest_kpis()

if not k:
    st.error("No KPI snapshot found in `dashboard_kpis`. Run compute_dashboard_kpis.py first.")
    st.stop()

# Base metrics
total_videos = int(k.get("total_videos", 0))
total_channels = int(k.get("total_channels", 0))
tracking_active = int(k.get("tracking_active", 0))

completed_total = int(k.get("completed_total", 0))
completed_age24 = int(k.get("completed_age24", 0))
completed_removed = int(k.get("completed_removed", 0))

stopped_low_quality = int(k.get("stopped_low_quality", 0))

# ML metrics (may be 0 if worker version cũ)
ml_3h_scored = int(k.get("ml_3h_scored", 0))
ml_6h_scored = int(k.get("ml_6h_scored", 0))
lowq_3h_flag = int(k.get("low_quality_flagged_3h", 0))
lowq_6h_flag = int(k.get("low_quality_flagged_6h", 0))

# ---------------- Title ----------------

st.title("🎥 Videos — Tracking Overview")
st.markdown("---")

# ---------------- Global Stats ----------------

st.subheader("📊 Global Stats")

g1, g2, g3 = st.columns(3)
with g1:
    stat_card("Total Videos", total_videos, total_videos or 1, "total videos", "📊")
with g2:
    stat_card("Total Channels", total_channels, total_videos or 1, "total videos", "📺")
with g3:
    stat_card("Tracking Active", tracking_active, total_videos or 1, "total videos", "📡")

# ---------------- Tracking Status ----------------

st.subheader("⏱️ Tracking Status")

c1, c2 = st.columns(2)

with c1:
    percent_completed = pct(completed_total, total_videos or 1)

    completed_html = f"""
    <div style="padding:16px 18px;
                border-radius:16px;
                background:#ffffff;
                border:1px solid #f0f0f0;
                box-shadow:0 2px 8px rgba(0,0,0,0.04);">
      <div style="font-size:18px; font-weight:600; margin-bottom:4px;">
        ✅ Completed
      </div>
      <div style="font-size:26px; font-weight:800; margin-bottom:0px;">
        {completed_total:,}
      </div>
      <div style="font-size:13px; color:#666; margin-top:-2px;">
        {percent_completed}% of total videos
      </div>

      <div style="background:#eee; height:6px; width:100%; border-radius:4px; margin-top:4px;">
        <div style="
            background:#ff6600;
            height:6px;
            width:{percent_completed}%;
            border-radius:4px;
            transition: width 0.4s ease-out;
        "></div>
      </div>

      <div style="display:flex; gap:8px; margin-top:10px; font-size:13px; color:#444;">
        <div style="
            flex:1;
            padding:6px 8px;
            border-radius:10px;
            background:#f8f8f8;
            border:1px solid #eeeeee;">
          Finish 24h cycle<br/><b>{completed_age24:,}</b>
        </div>
        <div style="
            flex:1;
            padding:6px 8px;
            border-radius:10px;
            background:#f8f8f8;
            border:1px solid #eeeeee;">
          Removed / deleted<br/><b>{completed_removed:,}</b>
        </div>
      </div>
    </div>
    """

    st.markdown(completed_html, unsafe_allow_html=True)


with c2:
    stat_card(
        "Stopped (Low Quality reason)",
        stopped_low_quality,
        total_videos or 1,
        "total videos",
        "🚫",
    )

# ---------------- Low-quality ML Models (3h / 6h) ----------------

st.subheader("🤖 Low-quality ML Models (3h / 6h)")

m1, m2 = st.columns(2)

with m1:
    base_3h = ml_3h_scored or total_videos or 1
    label_3h = "3h-scored videos" if ml_3h_scored else "total videos"
    stat_card("❌ Low-quality flagged at 3h", lowq_3h_flag, base_3h, label_3h, "")

with m2:
    base_6h = ml_6h_scored or total_videos or 1
    label_6h = "6h-scored videos" if ml_6h_scored else "total videos"
    stat_card("❌ Low-quality flagged at 6h", lowq_6h_flag, base_6h, label_6h, "")

st.markdown("---")

# ---------------- Latest 200 Videos ----------------

st.subheader("📄 Latest 200 Videos")

rows = get_recent_videos(limit=10)

if not rows:
    st.info("No video data found.")
else:
    # Header
    h1, h2, h3, h4 = st.columns([2, 5, 3, 1])
    h1.markdown("**Video ID**")
    h2.markdown("**Title**")
    h3.markdown("**Channel**")
    h4.markdown("**Action**")

    details_container = st.container()

    # danh sách video + nút View
    for idx, row in enumerate(rows):
        c1, c2, c3, c4 = st.columns([2, 5, 3, 1])

        c1.code(row["video_id"], language=None)
        c2.write(row["title"])
        c3.write(row["channel"])

        if c4.button("View", key=f"view_{idx}"):
            st.session_state["selected_video_id"] = row["video_id"]

# khối hiển thị chi tiết gọn
with details_container:
    selected_id = st.session_state.get("selected_video_id")
    if not selected_id:
        st.caption("Click **View** next to a video to see details.")
    else:
        doc = videos_col.find_one({"_id": selected_id})
        if not doc:
            st.warning(f"Video `{selected_id}` not found in database.")
        else:
            snippet = doc.get("snippet", {}) or {}
            tracking = doc.get("tracking", {}) or {}
            ml_flags = doc.get("ml_flags", {}) or {}
            source = doc.get("source", {}) or {}

            low3 = (ml_flags.get("low_quality_v1_3h") or {})
            low6 = (ml_flags.get("low_quality_v3_6h") or {})
            viral = (ml_flags.get("viral_v1") or {})

            # thời gian tracking
            import datetime
            now = datetime.datetime.utcnow()
            started = tracking.get("started_at")
            tracked_for = "—"
            if started:
                try:
                    started_dt = datetime.datetime.fromisoformat(started.replace("Z", "+00:00"))
                    delta = now - started_dt
                    tracked_for = str(delta).split(".")[0]  # bỏ microseconds
                except:
                    pass

            # region + query
            region = source.get("regionCode", "—")
            query_used = (
                source.get("query")
                or source.get("query_raw")
                or source.get("search_query")
                or "—"
            )

            st.markdown("---")
            st.subheader("🔍 Video details")

            # Info top
            st.markdown(f"**ID:** `{selected_id}`")
            st.markdown(f"**Title:** {snippet.get('title') or '—'}")
            st.markdown(f"**Channel:** {snippet.get('channelTitle') or '—'}")
            st.markdown(f"**Published at:** `{snippet.get('publishedAt', '—')}`")

            # region + query
            st.markdown(f"**Region code:** `{region}`")
            st.markdown(f"**Query used:** `{query_used}`")
            st.markdown(f"**Tracked for:** `{tracked_for}`")

            col_a, col_b, col_c = st.columns(3)

            # Tracking info
            with col_a:
                st.markdown("**Tracking**")
                st.markdown(
                    f"- Status: `{tracking.get('status', 'unknown')}`\n"
                    f"- Stop reason: `{tracking.get('stop_reason', '—')}`\n"
                    f"- Started at: `{tracking.get('started_at', '—')}`\n"
                    f"- Tracked for: `{tracked_for}`"
                )

            def fmt_score(x):
                return f"{x:.4f}" if isinstance(x, (int, float)) else "—"

            # LowQ 3h
            with col_b:
                st.markdown("**Low-quality 3h**")
                st.markdown(
                    f"- Score: `{fmt_score(low3.get('score'))}`\n"
                    f"- Threshold: `{fmt_score(low3.get('threshold'))}`\n"
                    f"- is_low: `{low3.get('is_low', '—')}`"
                )

            # LowQ 6h
            with col_c:
                st.markdown("**Low-quality 6h**")
                st.markdown(
                    f"- Score: `{fmt_score(low6.get('score'))}`\n"
                    f"- Threshold: `{fmt_score(low6.get('threshold'))}`\n"
                    f"- is_low: `{low6.get('is_low', '—')}`"
                )

            # Viral
            st.markdown("**Viral v1**")
            st.markdown(
                f"- Score: `{fmt_score(viral.get('score'))}`\n"
                f"- Likely: `{viral.get('likely', '—')}`\n"
                f"- Confirmed: `{viral.get('confirmed', '—')}`"
            )

