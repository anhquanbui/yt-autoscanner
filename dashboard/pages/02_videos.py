#!/usr/bin/env python3
"""
Streamlit page: 02_Videos (status overview)

- Total Videos / Channels / Tracking Active
- Tracking Status (Completed breakdown + Stopped low-quality)
- Low-quality ML Models (3h / 6h) — flagged counts
- Viral ML Models (6h / 12h / 24h / Final) — non-overlapping stage buckets
- Random 10 videos (video_id, title, channel, status + View)
"""

from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st
import pandas as pd
import datetime

import sys
from pathlib import Path

# Go from .../dashboard/pages/02_videos.py -> .../yt-autoscanner
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.db import get_db  # _resolve_db_name không dùng nên bỏ


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


def format_status_badge(status: str | None, stop_reason: str | None) -> str:
    """Emoji + text cho trạng thái tracking."""
    if status == "tracking":
        return "🟢 tracking"
    if status == "complete":
        return "✅ complete"
    if status == "stopped":
        if stop_reason and stop_reason.startswith("ml.low_quality"):
            return "⛔ stopped (low-quality)"
        elif stop_reason:
            return f"⛔ stopped ({stop_reason})"
        else:
            return "⛔ stopped"
    if status:
        return f"⚪ {status}"
    return "⚪ unknown"


def get_latest_kpis() -> Dict[str, Any] | None:
    """Latest snapshot from dashboard_kpis."""
    return kpis_col.find_one(sort=[("_id", -1)])


def get_random_videos(limit: int = 10) -> List[Dict[str, Any]]:
    """Random N videos cho bảng bên dưới."""
    pipeline = [
        {"$sample": {"size": limit}},
        {
            "$project": {
                "_id": 1,
                "snippet.title": 1,
                "snippet.channelTitle": 1,
                "tracking.status": 1,
                "tracking.stop_reason": 1,
            }
        },
    ]

    rows: List[Dict[str, Any]] = []
    for doc in videos_col.aggregate(pipeline):
        snippet = doc.get("snippet", {}) or {}
        tracking = doc.get("tracking", {}) or {}
        rows.append(
            {
                "video_id": doc.get("_id"),
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
                "status": tracking.get("status"),
                "stop_reason": tracking.get("stop_reason"),
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

# ML metrics (low-quality)
ml_3h_scored = int(k.get("ml_3h_scored", 0))
ml_6h_scored = int(k.get("ml_6h_scored", 0))
lowq_3h_flag = int(k.get("low_quality_flagged_3h", 0))
lowq_6h_flag = int(k.get("low_quality_flagged_6h", 0))

# Viral v1 metrics
viral_likely = int(k.get("viral_likely", 0))
viral_confirmed = int(k.get("viral_confirmed", 0))

# Viral v2 stage metrics (non-overlapping buckets)
viral2_stage_6h_only = int(k.get("viral2_stage_6h_only", 0))
viral2_stage_12h_only = int(k.get("viral2_stage_12h_only", 0))
viral2_stage_24h_only = int(k.get("viral2_stage_24h_only", 0))
viral2_final_decided = int(k.get("viral2_final_decided", 0))

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

# ---------------- Viral ML Models (6h / 12h / 24h / Final) ----------------

st.subheader("🔥 Viral ML Models (6h / 12h / 24h / Final)")

v1, v2, v3, v4 = st.columns(4)

with v1:
    stat_card(
        "🕕 Viral 6h Scored (stage)",
        viral2_stage_6h_only,
        total_videos or 1,
        "total videos",
    )

with v2:
    stat_card(
        "🕛 Viral 12h Scored (stage)",
        viral2_stage_12h_only,
        total_videos or 1,
        "total videos",
    )

with v3:
    stat_card(
        "🌙 Viral 24h Scored (stage)",
        viral2_stage_24h_only,
        total_videos or 1,
        "total videos",
    )

with v4:
    stat_card(
        "🏁 Finalized (Viral/Non/Unk)",
        viral2_final_decided,
        total_videos or 1,
        "total videos",
    )

st.markdown("---")

# ---------------- Random 10 Videos ----------------

st.subheader("📄 Random 10 Videos (tracking sample)")

header_left, header_right = st.columns([6, 1])
with header_left:
    st.caption("Random sample of 10 videos across the DB — good to spot-check tracking states.")
with header_right:
    if st.button("🎲 Shuffle", key="shuffle_videos", use_container_width=True):
        st.experimental_rerun()

# container cho phần details (luôn tồn tại, kể cả khi không có rows)
details_container = st.container()

rows = get_random_videos(limit=10)

if not rows:
    st.info("No video data found.")
else:
    # Header của danh sách: ID | Title | Channel | Status | Action
    h1, h2, h3, h4, h5 = st.columns([2, 5, 3, 2, 1])
    h1.markdown("**Video ID**")
    h2.markdown("**Title**")
    h3.markdown("**Channel**")
    h4.markdown("**📡 Tracking**")
    h5.markdown("**Action**")

    for idx, row in enumerate(rows):
        c1, c2, c3, c4, c5 = st.columns([2, 5, 3, 2, 1])

        c1.code(row["video_id"], language=None)
        c2.write(row["title"] or "")
        c3.write(row["channel"] or "—")

        status_badge = format_status_badge(row.get("status"), row.get("stop_reason"))
        c4.markdown(status_badge)

        if c5.button("View", key=f"view_{idx}"):
            st.session_state["selected_video_id"] = row["video_id"]

# --------- khối hiển thị chi tiết gọn ---------
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
            snapshots = doc.get("stats_snapshots", []) or []

            low3 = (ml_flags.get("low_quality_v1_3h") or {})
            low6 = (ml_flags.get("low_quality_v3_6h") or {})
            viral = (ml_flags.get("viral_v1") or {})

            # ===== thời gian tracking =====
            now = datetime.datetime.utcnow()
            started = tracking.get("started_at")
            tracked_for = "-"
            started_str = "-"

            if started:
                try:
                    started_dt = datetime.datetime.fromisoformat(
                        started.replace("Z", "+00:00")
                    )
                    delta = now - started_dt
                    tracked_for = str(delta).split(".")[0]
                    started_str = started
                except Exception:
                    tracked_for = "n/a (invalid timestamp)"
                    started_str = started
            else:
                if tracking.get("status") == "complete":
                    tracked_for = "n/a (completed; start time not stored)"
                else:
                    tracked_for = "n/a (not started yet)"

            # ===== region + query =====
            region = source.get("regionCode") or "not recorded"
            query_used = (
                source.get("query")
                or source.get("query_raw")
                or source.get("search_query")
                or "not recorded"
            )

            # ===== snapshot / trending =====
            num_snaps = len(snapshots)
            last_snap = snapshots[-1] if num_snaps >= 1 else None
            prev_snap = snapshots[-2] if num_snaps >= 2 else None

            def get_ts(s):
                if not s:
                    return None
                ts = s.get("ts") or s.get("timestamp")
                if not ts:
                    return None
                try:
                    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    return None

            def get_views(s):
                if not s:
                    return None
                return (
                    s.get("viewCount")
                    or s.get("views")
                    or s.get("statistics", {}).get("viewCount")
                )

            def get_likes(s):
                if not s:
                    return None
                return (
                    s.get("likeCount")
                    or s.get("likes")
                    or s.get("statistics", {}).get("likeCount")
                )

            last_ts = get_ts(last_snap)
            prev_ts = get_ts(prev_snap)
            last_views = get_views(last_snap)
            last_likes = get_likes(last_snap)

            view_delta = None
            like_delta = None
            view_per_hour = None
            like_per_hour = None

            if last_snap and prev_snap and last_ts and prev_ts:
                dt_hours = (last_ts - prev_ts).total_seconds() / 3600.0
                if dt_hours > 0:
                    if last_views is not None and get_views(prev_snap) is not None:
                        view_delta = last_views - get_views(prev_snap)
                        view_per_hour = view_delta / dt_hours
                    if last_likes is not None and get_likes(prev_snap) is not None:
                        like_delta = last_likes - get_likes(prev_snap)
                        like_per_hour = like_delta / dt_hours

            def fmt_int(x):
                return f"{int(x):,}" if isinstance(x, (int, float)) else "-"

            def fmt_float(x):
                return f"{x:.2f}" if isinstance(x, (int, float)) else "-"

            def fmt_score(x):
                return f"{x:.4f}" if isinstance(x, (int, float)) else "-"

            # last snapshot time
            if last_ts:
                last_ts_str = last_ts.isoformat()
            elif num_snaps == 0:
                last_ts_str = "no snapshots yet"
            else:
                last_ts_str = "-"

            # likes hiển thị rõ hơn
            if last_likes is None:
                last_likes_str = "🔒 hidden / not available"
            else:
                last_likes_str = fmt_int(last_likes)

            # delta & per-hour
            if num_snaps < 2:
                view_delta_str = "🛏 not enough snapshots"
                like_delta_str = "🛏 not enough snapshots"
                view_per_hour_str = "🛏 not enough snapshots"
                like_per_hour_str = "🛏 not enough snapshots"
            else:
                view_delta_str = fmt_int(view_delta) if view_delta is not None else "-"
                if like_delta is None and (last_likes is None or get_likes(prev_snap) is None):
                    like_delta_str = "🔒 likes hidden / not available"
                else:
                    like_delta_str = fmt_int(like_delta)

                view_per_hour_str = (
                    fmt_float(view_per_hour) if view_per_hour is not None else "-"
                )
                if like_per_hour is None and (last_likes is None or get_likes(prev_snap) is None):
                    like_per_hour_str = "🔒 likes hidden / not available"
                else:
                    like_per_hour_str = (
                        fmt_float(like_per_hour) if like_per_hour is not None else "-"
                    )

            low3_score_str = (
                fmt_score(low3.get("score"))
                if low3.get("score") is not None
                else "not scored yet"
            )
            low3_thr_str = (
                fmt_score(low3.get("threshold"))
                if low3.get("threshold") is not None
                else "-"
            )
            low6_score_str = (
                fmt_score(low6.get("score"))
                if low6.get("score") is not None
                else "not scored yet"
            )
            low6_thr_str = (
                fmt_score(low6.get("threshold"))
                if low6.get("threshold") is not None
                else "-"
            )

            # ===== render details =====
            st.markdown("---")
            st.subheader("🔍 Video details")

            st.markdown(f"**ID:** `{selected_id}`")
            st.markdown(f"**Title:** {snippet.get('title') or '—'}")
            st.markdown(f"**Channel:** {snippet.get('channelTitle') or '—'}")
            st.markdown(f"**Published at:** `{snippet.get('publishedAt', '—')}`")
            st.markdown(f"**Region code:** `{region}`")
            st.markdown(f"**Query used:** `{query_used}`")
            st.markdown(f"**Tracked for:** `{tracked_for}`")

            col_a, col_b, col_c = st.columns(3)

            with col_a:
                st.markdown("**Tracking**")
                st.markdown(
                    f"- Status: `{tracking.get('status', 'unknown')}`\n"
                    f"- Stop reason: `{tracking.get('stop_reason', '—')}`\n"
                    f"- Started at: `{started_str}`\n"
                    f"- Tracked for: `{tracked_for}`"
                )

            with col_b:
                st.markdown("**Low-quality 3h**")
                st.markdown(
                    f"- Score: `{low3_score_str}`\n"
                    f"- Threshold: `{low3_thr_str}`\n"
                    f"- is_low: `{low3.get('is_low', '—')}`"
                )

            with col_c:
                st.markdown("**Low-quality 6h**")
                st.markdown(
                    f"- Score: `{low6_score_str}`\n"
                    f"- Threshold: `{low6_thr_str}`\n"
                    f"- is_low: `{low6.get('is_low', '—')}`"
                )

            st.markdown("**Viral v1**")
            st.markdown(
                f"- Score: `{fmt_score(viral.get('score'))}`\n"
                f"- Likely: `{viral.get('likely', '—')}`\n"
                f"- Confirmed: `{viral.get('confirmed', '—')}`"
            )

            st.markdown("**📈 Engagement snapshots**")
            st.markdown(
                f"- Total snapshots: `{num_snaps}`\n"
                f"- Last snapshot time (YouTube API): `{last_ts_str}`\n"
                f"- Last views: `{fmt_int(last_views)}`\n"
                f"- Last likes: `{last_likes_str}`\n"
                f"- Δ Views (last two snaps): `{view_delta_str}`\n"
                f"- Δ Likes (last two snaps): `{like_delta_str}`\n"
                f"- Views per hour (last interval): `{view_per_hour_str}`\n"
                f"- Likes per hour (last interval): `{like_per_hour_str}`"
            )
