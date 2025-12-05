import math
from typing import Dict, Any, List

import pandas as pd
import streamlit as st

from config.db import get_db
from config.env import load_env

# Ensure .env is loaded exactly once
load_env()

# ============================================================
# 0. Simple styling for this page
# ============================================================
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {
    background-color: #f9fafb;
}
.main .block-container {
    max-width: 1200px;
    padding-top: 2rem;
}

/* Small pill chips used in legend */
.viral-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    margin-right: 6px;
    margin-bottom: 4px;
}
.viral-pill-weak {
    background: #fef3c7;  /* amber-100 */
    color: #92400e;       /* amber-800 */
}
.viral-pill-viral {
    background: #dbeafe;  /* blue-100 */
    color: #1d4ed8;       /* blue-700 */
}
.viral-pill-super {
    background: #fee2e2;  /* red-100 */
    color: #b91c1c;       /* red-700 */
}
.behavior-box {
    background: #ffffff;
    border-radius: 12px;
    border: 1px solid #e5e7eb;
    padding: 14px 16px;
    margin-top: 0.75rem;
    box-shadow: 0 2px 6px rgba(15,23,42,0.04);
}
.behavior-title {
    font-size: 0.9rem;
    font-weight: 600;
    color: #4b5563;
    margin-bottom: 4px;
}
.behavior-text {
    font-size: 0.86rem;
    color: #4b5563;
    line-height: 1.5;
}
</style>
""",
    unsafe_allow_html=True,
)

# Mapping for nicer labels
STATUS_LABELS = {
    "weak_viral": "🟡 Weak viral",
    "viral": "🔵 Viral",
    "super_viral": "🔴 Super viral",
    "non_viral": "⚪ Non-viral",
    "non_viral_lowq": "⚪ Non-viral (low quality block)",
    "viral_after_removed": "🟣 Viral after removed",
    "removed": "⚫ Removed",
    "unknown": "⚪ Unknown / no decision",
    None: "⚪ Unknown / no decision",
}

BEHAVIOR_LABELS = {
    "no_signal": "⚪ No signal",
    "early_peak": "🕒 Early peak",
    "late_growth": "🌱 Late growth",
    "consistent": "📈 Consistent",
    "volatile": "🌪️ Volatile",
    "neutral": "〰️ Neutral / unclear",
    None: "⚪ Unknown",
}

# ============================================================
# 1. Mongo helpers (cached)
# ============================================================

@st.cache_resource(show_spinner=False)
def get_coll():
    """
    Return the Mongo collection object for `videos`.
    """
    db = get_db()
    return db["videos"]


@st.cache_data(show_spinner=False)
def load_keywords() -> List[str]:
    """
    Return list of distinct keywords for the Keyword filter.

    Prefer dashboard_kpis.filter_keywords snapshot (lighter),
    fallback to aggregate directly from videos if missing.
    """
    db = get_db()
    kpis = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    if kpis and "filter_keywords" in kpis:
        kws = kpis.get("filter_keywords") or []
        return [d.get("query") for d in kws if d.get("query")]

    coll = get_coll()
    pipeline = [
        {"$match": {"source.query": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$source.query", "video_count": {"$sum": 1}}},
        {"$sort": {"video_count": -1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline)]


@st.cache_data(show_spinner=False)
def load_regions_all() -> List[str]:
    """
    Return ALL distinct region codes for the Region filter.

    Prefer dashboard_kpis.filter_regions snapshot (lighter),
    fallback to aggregate directly from videos if missing.
    """
    db = get_db()
    kpis = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    if kpis and "filter_regions" in kpis:
        regs = kpis.get("filter_regions") or []
        return [d.get("region") for d in regs if d.get("region")]

    coll = get_coll()
    pipeline = [
        {"$match": {"source.regionCode": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$source.regionCode"}},
        {"$sort": {"_id": 1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline) if doc["_id"]]


def query_videos(filters: Dict[str, Any], page: int, page_size: int):
    """
    Query videos with the given Mongo filters and pagination.
    """
    coll = get_coll()

    total = coll.count_documents(filters)
    skip = (page - 1) * page_size

    projection = {
        "_id": 1,
        "video_id": 1,
        "videoId": 1,
        "title": 1,
        "snippet.title": 1,
        "source.title": 1,
        "tracking.status": 1,
        "ml_flags.viral_v2.final.status": 1,
        "ml_flags.viral_v2.final.behavior": 1,
    }

    cursor = (
        coll.find(filters, projection=projection)
        .sort([("latest_stats_ts", -1), ("_id", -1)])
        .skip(skip)
        .limit(page_size)
    )

    rows: List[Dict[str, Any]] = []
    for doc in cursor:
        source = doc.get("source") or {}
        snippet = doc.get("snippet") or {}
        tracking = doc.get("tracking") or {}
        ml_flags = doc.get("ml_flags") or {}
        viral_v2 = ml_flags.get("viral_v2") or {}
        final_info = viral_v2.get("final") or {}

        video_id = (
            doc.get("video_id")
            or doc.get("videoId")
            or str(doc.get("_id"))
        )

        title = (
            doc.get("title")
            or snippet.get("title")
            or source.get("title")
        )

        raw_status = final_info.get("status")
        raw_behavior = final_info.get("behavior")

        status = STATUS_LABELS.get(raw_status, raw_status or "Unknown / no decision")
        behavior = BEHAVIOR_LABELS.get(raw_behavior, raw_behavior or "Unknown")

        rows.append(
            {
                "video_id": video_id,
                "title": title,
                "status": tracking.get("status"),
                "final_status": status,
                "behavior": behavior,
                "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )

    return rows, total


# ============================================================
# 2. Page layout
# ============================================================
st.title("🚀 Viral Filter")

st.markdown(
    """
Use this page to explore **final viral decisions**:

- Filter videos by discovery **keyword**, **region code**, and **final viral status**.
- Inspect the **Behavior** tag to understand the temporal pattern of virality
  (early peak, late growth, consistent, volatile, …).
"""
)

# Legend: statuses + behavior explanation
st.markdown(
    """
<div class="behavior-box">
  <div class="behavior-title">Behavior (temporal pattern from 6h → 12h → 24h)</div>
  <div class="behavior-text">
    <b>No signal</b> – all stages look non-viral.<br/>
    <b>Early peak</b> – strong viral signal at 6–12h but weak/non-viral by 24h.<br/>
    <b>Late growth</b> – quiet at 6–12h, becomes viral at 24h.<br/>
    <b>Consistent</b> – same viral label across 6h/12h/24h (stable trajectory).<br/>
    <b>Volatile</b> – labels change between stages (unstable / noisy pattern).<br/>
    <b>Neutral</b> – pattern doesn’t clearly match the above cases.
  </div>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("---")

# ------------------------------------------------------------
# 2.1 Filters
# ------------------------------------------------------------
st.subheader("🎛 Filters")

keyword_list = load_keywords()
keyword_options = ["(All)"] + keyword_list

region_list = load_regions_all()
region_options = ["(All)"] + region_list

viral_options = [
    "(All)",
    "Final: any viral (weak→super)",
    "Final: weak viral",
    "Final: viral",
    "Final: super viral",
]

behavior_options = [
    "(All)",
    "No signal",
    "Early peak",
    "Late growth",
    "Consistent",
    "Volatile",
    "Neutral / unclear",
]

# Layout: keyword | region | viral | page size
col_k, col_r, col_v, col_bh, col_ps = st.columns([4, 3, 3, 3, 2])

with col_k:
    selected_keyword = st.selectbox("Keyword", keyword_options)

with col_r:
    selected_region = st.selectbox("Region Code", region_options)

with col_v:
    selected_viral = st.selectbox("Final viral status", viral_options)

with col_ps:
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1)
    
with col_bh:
    selected_behavior = st.selectbox("Behavior pattern", behavior_options)

filters: Dict[str, Any] = {}
has_kw = selected_keyword != "(All)"
has_rg = selected_region != "(All)"
has_vl = selected_viral != "(All)"
has_bh = selected_behavior != "(All)"

if has_kw:
    filters["source.query"] = selected_keyword

if has_rg:
    filters["source.regionCode"] = selected_region

if has_vl:
    key = "ml_flags.viral_v2.final.status"

    if selected_viral == "Final: any viral (weak→super)":
        filters[key] = {"$in": ["weak_viral", "viral", "super_viral"]}
    elif selected_viral == "Final: weak viral":
        filters[key] = "weak_viral"
    elif selected_viral == "Final: viral":
        filters[key] = "viral"
    elif selected_viral == "Final: super viral":
        filters[key] = "super_viral"

if has_bh:
    key = "ml_flags.viral_v2.final.behavior"
    # Map UI text → stored label
    map_behavior = {
        "No signal": "no_signal",
        "Early peak": "early_peak",
        "Late growth": "late_growth",
        "Consistent": "consistent",
        "Volatile": "volatile",
        "Neutral / unclear": "neutral",
    }
    filters[key] = map_behavior.get(selected_behavior)


st.markdown("---")

# ------------------------------------------------------------
# 2.2 Video results
# ------------------------------------------------------------
st.subheader("📼 Video Results")

if not has_kw and not has_rg and not has_vl:
    st.info(
        "Please select at least a **keyword**, **region code** or "
        "**final viral status** to view videos."
    )
else:
    if "page" not in st.session_state:
        st.session_state.page = 1

    filter_key = f"{selected_keyword}|{selected_region}|{selected_viral}|{page_size}"
    if st.session_state.get("filter_key") != filter_key:
        st.session_state.filter_key = filter_key
        st.session_state.page = 1
        st.experimental_rerun()

    rows, total = query_videos(filters, st.session_state.page, page_size)

    if total == 0:
        st.info("No matching videos.")
    else:
        max_page = max(1, math.ceil(total / page_size))

        col_info, col_page = st.columns([3, 2])
        with col_info:
            st.write(
                f"Found **{total:,}** videos · "
                f"Page {st.session_state.page}/{max_page}"
            )

        with col_page:
            new_page = st.number_input(
                "Page",
                min_value=1,
                max_value=max_page,
                value=st.session_state.page,
                step=1,
            )
            if new_page != st.session_state.page:
                st.session_state.page = new_page
                st.experimental_rerun()

        df = pd.DataFrame(rows)

        df = df.rename(
            columns={
                "video_id": "Video ID",
                "title": "Title",
                "status": "Tracking status",
                "final_status": "Final status",
                "behavior": "Behavior",
                "youtube_url": "Open",
            }
        )

        df = df[
            ["Video ID", "Title", "Tracking status", "Final status", "Behavior", "Open"]
        ]

        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Open": st.column_config.LinkColumn(
                    "Open in browser",
                    display_text="Open",
                )
            },
        )
