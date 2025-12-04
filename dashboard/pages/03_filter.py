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
    Return list of distinct keywords for the Keyword filter.

    Ưu tiên đọc từ dashboard_kpis.filter_keywords (snapshot đã
    được compute_dashboard_kpis chuẩn bị sẵn để trang Filter nhẹ hơn).

    Nếu chưa có (ví dụ worker KPI chưa chạy version mới),
    fallback về aggregate trực tiếp trên collection `videos`.
    """
    db = get_db()
    kpis = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    # --- Preferred path: dùng snapshot từ KPI ---
    if kpis and "filter_keywords" in kpis:
        kws = kpis.get("filter_keywords") or []
        return [d.get("query") for d in kws if d.get("query")]

    # --- Fallback: aggregate trực tiếp trên videos (schema cũ) ---
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
    Return ALL distinct region codes for the Region filter.

    Ưu tiên đọc từ dashboard_kpis.filter_regions.

    Nếu chưa có, fallback về aggregate trực tiếp trên `videos`.
    """
    db = get_db()
    kpis = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    # --- Preferred path: dùng snapshot từ KPI ---
    if kpis and "filter_regions" in kpis:
        regs = kpis.get("filter_regions") or []
        return [d.get("region") for d in regs if d.get("region")]

    # --- Fallback: aggregate trực tiếp ---
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

    # Projection để giảm lượng dữ liệu trả về cho mỗi doc
    projection = {
        "_id": 1,
        "video_id": 1,
        "videoId": 1,
        "title": 1,
        "snippet.title": 1,
        "source.title": 1,
        "tracking.status": 1,
        "ml_flags.viral_v2.final.status": 1,
        "ml_flags.ad_friendly_v1.label": 1,
    }

    cursor = (
        coll.find(filters, projection=projection)
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
        ml_flags = doc.get("ml_flags") or {}
        viral_v2 = ml_flags.get("viral_v2") or {}
        final_info = viral_v2.get("final") or {}
        ad_info = ml_flags.get("ad_friendly_v1") or {}

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
        final_status = final_info.get("status") or "unknown"

        raw_ad_label = ad_info.get("label")
        if raw_ad_label == "AD_FRIENDLY":
            ad_label = "Ad-friendly"
        elif raw_ad_label == "NON_AD_FRIENDLY":
            ad_label = "Non ad-friendly"
        else:
            ad_label = "Unknown"

        rows.append(
            {
                "video_id": video_id,
                "title": title,
                "status": status,
                "final_status": final_status,
                "ad_friendly": ad_label,
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
Filter your videos by **keyword**, **region code**, **final viral status** and **ad-friendly label**,  
then browse matching results with pagination.
"""
)

st.markdown("---")

# ------------------------------------------------------------
# 2.1 Filters
# ------------------------------------------------------------

st.subheader("🎛 Filters")

# Keyword dropdown (sorted by video_count desc via KPI snapshot)
keyword_list = load_keywords()
keyword_options = ["(All)"] + keyword_list

# Region dropdown (all distinct source.regionCode via KPI snapshot)
region_list = load_regions_all()
region_options = ["(All)"] + region_list

# Viral filter options mapped to ml_flags.viral_v2.final.status
viral_options = [
    "(All)",
    "Final: any viral (weak→super)",
    "Final: weak viral",
    "Final: viral",
    "Final: super viral",
    "Final: non viral",
    "Final: non viral (lowq)",
    "Final: viral_after_removed",
    "Final: removed",
    "Final: unknown / no decision",
]

# Ad-friendly filter options mapped to ml_flags.ad_friendly_v1.label
ad_options = [
    "(All)",
    "Ad-friendly only",
    "Non ad-friendly only",
    "Unknown / not scored",
]

# Layout: keyword | region | viral | ad | page size
col_k, col_r, col_v, col_ad, col_ps = st.columns([4, 3, 3, 3, 2])

with col_k:
    # Filter by discovery keyword (source.query)
    selected_keyword = st.selectbox("Keyword (source.query)", keyword_options)

with col_r:
    # Filter by source.regionCode
    selected_region = st.selectbox("Region Code (source.regionCode)", region_options)

with col_v:
    # Filter by viral_v2.final.status
    selected_viral = st.selectbox("Final viral status", viral_options)

with col_ad:
    # Filter by ad-friendly label (ml_flags.ad_friendly_v1.label)
    selected_ad = st.selectbox("Ad-friendly status", ad_options)

with col_ps:
    # Control for rows per page (page size)
    page_size = st.selectbox("Rows per page", [25, 50, 100], index=1)

# Build filters for Mongo query based on UI selections
filters: Dict[str, Any] = {}
has_kw = selected_keyword != "(All)"
has_rg = selected_region != "(All)"
has_vl = selected_viral != "(All)"
has_ad = selected_ad != "(All)"

if has_kw:
    filters["source.query"] = selected_keyword

if has_rg:
    filters["source.regionCode"] = selected_region

# Map viral filter UI -> Mongo filter on ml_flags.viral_v2.final.status
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

    elif selected_viral == "Final: non viral":
        filters[key] = "non_viral"

    elif selected_viral == "Final: non viral (lowq)":
        filters[key] = "non_viral_lowq"

    elif selected_viral == "Final: viral_after_removed":
        filters[key] = "viral_after_removed"

    elif selected_viral == "Final: removed":
        filters[key] = "removed"

    elif selected_viral == "Final: unknown / no decision":
        filters[key] = "unknown"

# Map ad-friendly filter UI -> Mongo filter on ml_flags.ad_friendly_v1.label
if has_ad:
    key = "ml_flags.ad_friendly_v1.label"

    if selected_ad == "Ad-friendly only":
        filters[key] = "AD_FRIENDLY"

    elif selected_ad == "Non ad-friendly only":
        filters[key] = "NON_AD_FRIENDLY"

    elif selected_ad == "Unknown / not scored":
        # Label chưa được set hoặc field chưa tồn tại
        filters["$or"] = filters.get("$or", []) + [
            {"ml_flags.ad_friendly_v1": {"$exists": False}},
            {"ml_flags.ad_friendly_v1.label": None},
        ]

st.markdown("---")

# ------------------------------------------------------------
# 2.2 Video results
# ------------------------------------------------------------

st.subheader("📼 Video Results")

# If there is absolutely no filter, avoid loading the whole DB.
if not has_kw and not has_rg and not has_vl and not has_ad:
    st.info(
        "Please select at least a **keyword**, **region code**, **final viral status** "
        "or **ad-friendly status** to view videos."
    )
else:
    # Pagination state kept in st.session_state["page"]
    if "page" not in st.session_state:
        st.session_state.page = 1

    # filter_key tracks the current combination of filters + page_size.
    # Whenever it changes, we reset page to 1 and rerun.
    filter_key = f"{selected_keyword}|{selected_region}|{selected_viral}|{selected_ad}|{page_size}"
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
            "status": "Tracking status",
            "final_status": "Final status",
            "ad_friendly": "Ad-friendly",
            "youtube_url": "Open",
        })

        # Keep only the columns we want to show
        df = df[["Video ID", "Title", "Tracking status", "Final status", "Ad-friendly", "Open"]]

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
