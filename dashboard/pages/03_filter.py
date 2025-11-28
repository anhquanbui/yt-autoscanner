# 03_filter.py
import math
from typing import Dict, Any, List

import pandas as pd
import streamlit as st

from config.db import get_db
from config.env import load_env

# Ensure env loaded once
load_env()


# ============================================================
# 1. Mongo helpers (cached)
# ============================================================

@st.cache_resource(show_spinner=False)
def get_coll():
    db = get_db()
    return db["videos"]


@st.cache_data(show_spinner=False)
def load_keywords() -> List[str]:
    """
    Return list of keywords (source.query),
    sorted by video_count desc.
    """
    coll = get_coll()
    pipeline = [
        {"$match": {"source.query": {"$exists": True, "$ne": None}}},
        {
            "$group": {
                "_id": "$source.query",
                "video_count": {"$sum": 1},
            }
        },
        {"$sort": {"video_count": -1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline)]


@st.cache_data(show_spinner=False)
def load_regions_all() -> List[str]:
    """
    Return ALL distinct region codes (source.regionCode) in dataset,
    sorted alphabetically (global, not filtered by keyword).
    """
    coll = get_coll()
    pipeline = [
        {"$match": {"source.regionCode": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$source.regionCode"}},
        {"$sort": {"_id": 1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline) if doc["_id"]]


def query_videos(filters: Dict[str, Any], page: int, page_size: int):
    """
    Query videos with filters + pagination.
    """
    coll = get_coll()

    total = coll.count_documents(filters)
    skip = (page - 1) * page_size

    cursor = (
        coll.find(filters)
        .sort([("latest_stats_ts", -1), ("_id", -1)])
        .skip(skip)
        .limit(page_size)
    )

    rows: List[Dict[str, Any]] = []
    for doc in cursor:
        source = doc.get("source") or {}
        snippet = doc.get("snippet") or {}
        tracking = doc.get("tracking") or {}

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

        status = tracking.get("status")

        rows.append(
            {
                "video_id": video_id,
                "title": title,
                "status": status,
                "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )

    return rows, total


# ============================================================
# 2. Page layout
# ============================================================

st.set_page_config(
    page_title="Filter Videos",
    layout="wide",
)

st.title("🔍 Video Filter")

st.markdown(
    """
Filter your videos by **keyword**, **region code**, and **viral flags**,
then browse matching results with pagination.
"""
)

st.markdown("---")

# ------------------------------------------------------------
# 2.1 Filters
# ------------------------------------------------------------

st.subheader("🎛 Filters")

# Keyword dropdown (sorted by video_count desc)
keyword_list = load_keywords()
keyword_options = ["(All)"] + keyword_list

# Region dropdown (GLOBAL distinct source.regionCode)
region_list = load_regions_all()
region_options = ["(All)"] + region_list

# Viral filter options
viral_options = ["(All)", "Viral likely", "Viral confirmed"]

col_k, col_r, col_v, col_ps = st.columns([4, 3, 3, 2])

with col_k:
    selected_keyword = st.selectbox("Keyword (source.query)", keyword_options)

with col_r:
    selected_region = st.selectbox("Region Code (source.regionCode)", region_options)

with col_v:
    selected_viral = st.selectbox("Viral flag (ml_flags)", viral_options)

with col_ps:
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1)

# Build filters
filters: Dict[str, Any] = {}
has_kw = selected_keyword != "(All)"
has_rg = selected_region != "(All)"
has_vl = selected_viral != "(All)"

if has_kw:
    filters["source.query"] = selected_keyword

if has_rg:
    filters["source.regionCode"] = selected_region

# Map viral filter → Mongo query
if has_vl:
    if selected_viral == "Viral likely":
        # TODO: chỉnh lại field nếu schema khác
        filters["ml_flags.viral_v2.h6.is_candidate"] = True
    elif selected_viral == "Viral confirmed":
        filters["ml_flags.viral_v2.h12.is_viral_12h"] = True

st.markdown("---")

# ------------------------------------------------------------
# 2.2 Video results
# ------------------------------------------------------------

st.subheader("📼 Video Results")

# If no filter at all -> ask user to select something
if not has_kw and not has_rg and not has_vl:
    st.info("Please select at least a **keyword**, **region code**, or **viral flag** to view videos.")
else:
    # Pagination state
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

        df = df.rename(columns={
            "video_id": "Video ID",
            "title": "Title",
            "status": "Status",
            "youtube_url": "Open",
        })

        df = df[["Video ID", "Title", "Status", "Open"]]

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
