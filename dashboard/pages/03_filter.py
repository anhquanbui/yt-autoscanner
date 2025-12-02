# 03_filter.py
import math
from typing import Dict, Any, List

import pandas as pd
import streamlit as st

from config.db import get_db
from config.env import load_env

# Ensure .env is loaded exactly once for the whole app.
# This makes Mongo credentials / other env vars available.
load_env()


# ============================================================
# 1. Mongo helpers (cached)
# ============================================================

@st.cache_resource(show_spinner=False)
def get_coll():
    """
    Return the Mongo collection object for `videos`.

    We cache this as a resource so the Mongo client / collection
    are reused between reruns, instead of reconnecting each time.
    """
    db = get_db()
    return db["videos"]


@st.cache_data(show_spinner=False)
def load_keywords() -> List[str]:
    """
    Return list of distinct keywords from source.query, sorted
    by video_count descending.

    This powers the "Keyword" dropdown, with most-used queries first.
    """
    coll = get_coll()
    pipeline = [
        # Only consider documents where source.query exists and is not null
        {"$match": {"source.query": {"$exists": True, "$ne": None}}},
        {
            # Group by keyword and count how many videos use it
            "$group": {
                "_id": "$source.query",
                "video_count": {"$sum": 1},
            }
        },
        # Sort by count (desc) so most common keywords appear first
        {"$sort": {"video_count": -1}},
    ]
    return [doc["_id"] for doc in coll.aggregate(pipeline)]


@st.cache_data(show_spinner=False)
def load_regions_all() -> List[str]:
    """
    Return ALL distinct region codes (source.regionCode) present
    in the entire dataset, sorted alphabetically.

    This is global, not filtered by keyword – it scans all videos.
    """
    coll = get_coll()
    pipeline = [
        {"$match": {"source.regionCode": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$source.regionCode"}},
        {"$sort": {"_id": 1}},
    ]
    # Filter out empty codes just in case
    return [doc["_id"] for doc in coll.aggregate(pipeline) if doc["_id"]]


def query_videos(filters: Dict[str, Any], page: int, page_size: int):
    """
    Query videos with the given Mongo filters and pagination.

    Parameters
    ----------
    filters : dict
        MongoDB filter dict, built from the UI selections.
    page : int
        1-based page number.
    page_size : int
        Rows per page.

    Returns
    -------
    (rows, total) : (List[dict], int)
        rows  = list of simplified video records for the current page
        total = total number of matching documents (for pagination)
    """
    coll = get_coll()

    total = coll.count_documents(filters)
    skip = (page - 1) * page_size

    cursor = (
        coll.find(filters)
        # Sort by latest_stats_ts desc, then by _id desc for tie-breaking
        .sort([("latest_stats_ts", -1), ("_id", -1)])
        .skip(skip)
        .limit(page_size)
    )

    rows: List[Dict[str, Any]] = []
    for doc in cursor:
        source = doc.get("source") or {}
        snippet = doc.get("snippet") or {}
        tracking = doc.get("tracking") or {}

        # Try different possible field names for the video id,
        # fall back to stringified _id as a last resort.
        video_id = (
            doc.get("video_id")
            or doc.get("videoId")
            or str(doc.get("_id"))
        )

        # Title may be stored in different places depending on schema version.
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

# Region dropdown (all distinct source.regionCode in dataset)
region_list = load_regions_all()
region_options = ["(All)"] + region_list

# Viral filter options mapped to ml_flags.* fields
viral_options = [
    "(All)",
    "Stage: 6h candidate",
    "Stage: 12h confirmed",
    "Final: weak viral",
    "Final: viral",
    "Final: super viral",
    "Final: non viral",
    "Final: non viral (lowq)",
]

# Layout: keyword | region | viral | page size
col_k, col_r, col_v, col_ps = st.columns([4, 3, 3, 2])

with col_k:
    # Filter by discovery keyword (source.query)
    selected_keyword = st.selectbox("Keyword (source.query)", keyword_options)

with col_r:
    # Filter by source.regionCode
    selected_region = st.selectbox("Region Code (source.regionCode)", region_options)

with col_v:
    # Filter by viral flags stored in ml_flags
    selected_viral = st.selectbox("Viral flag (ml_flags)", viral_options)

with col_ps:
    # Control for rows per page (page size)
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1)

# Build filters for Mongo query based on UI selections
filters: Dict[str, Any] = {}
has_kw = selected_keyword != "(All)"
has_rg = selected_region != "(All)"
has_vl = selected_viral != "(All)"

if has_kw:
    filters["source.query"] = selected_keyword

if has_rg:
    filters["source.regionCode"] = selected_region

# Map viral filter UI -> Mongo filter on ml_flags fields
if has_vl:
    if selected_viral == "Stage: 6h candidate":
        # Candidate at 6h stage (early signal)
        filters["ml_flags.viral_v2.h6.is_candidate"] = True

    elif selected_viral == "Stage: 12h confirmed":
        # Confirmed viral at 12h stage
        filters["ml_flags.viral_v2.h12.is_viral_12h"] = True

    elif selected_viral == "Final: weak viral":
        filters["ml_flags.viral_v2.final.status"] = "weak_viral"

    elif selected_viral == "Final: viral":
        filters["ml_flags.viral_v2.final.status"] = "viral"

    elif selected_viral == "Final: super viral":
        filters["ml_flags.viral_v2.final.status"] = "super_viral"

    elif selected_viral == "Final: non viral":
        filters["ml_flags.viral_v2.final.status"] = "non_viral"

    elif selected_viral == "Final: non viral (lowq)":
        filters["ml_flags.viral_v2.final.status"] = "non_viral_lowq"

st.markdown("---")

# ------------------------------------------------------------
# 2.2 Video results
# ------------------------------------------------------------

st.subheader("📼 Video Results")

# If there is absolutely no filter, avoid loading the whole DB.
if not has_kw and not has_rg and not has_vl:
    st.info("Please select at least a **keyword**, **region code**, or **viral flag** to view videos.")
else:
    # Pagination state kept in st.session_state["page"]
    if "page" not in st.session_state:
        st.session_state.page = 1

    # filter_key tracks the current combination of filters + page_size.
    # Whenever it changes, we reset page to 1 and rerun.
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
            # Display total matches + current page / max page
            st.write(
                f"Found **{total:,}** videos · "
                f"Page {st.session_state.page}/{max_page}"
            )

        with col_page:
            # Number input to jump directly to a specific page
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

        # Convert result rows to DataFrame for Streamlit's dataframe widget
        df = pd.DataFrame(rows)

        # Rename columns for UI
        df = df.rename(columns={
            "video_id": "Video ID",
            "title": "Title",
            "status": "Status",
            "youtube_url": "Open",
        })

        # Keep only the columns we want to show
        df = df[["Video ID", "Title", "Status", "Open"]]

        # Render the table with a clickable link column for YouTube URL
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
