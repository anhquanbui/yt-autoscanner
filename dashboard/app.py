# app.py — YouTube Tracker — Local Dashboard (with Plotly & highlights)
import os, json
import streamlit as st
import pandas as pd
from datetime import timedelta

# Optional: Mongo
try:
    from pymongo import MongoClient
    PYMONGO_OK = True
except Exception:
    PYMONGO_OK = False

# Charts
import plotly.express as px
from dotenv import load_dotenv
load_dotenv()

st.set_page_config(page_title="YouTube Tracker — Local Dashboard", layout="wide")

# ------------------------------
# Helpers
# ------------------------------
@st.cache_data
def load_json(path="dashboard_summary.json"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = list(data.values())
    df = pd.DataFrame(data)
    return df

@st.cache_data(ttl=180)
def load_mongo(uri, db, coll):
    client = MongoClient(uri, tlsAllowInvalidCertificates=True)
    c = client[db][coll]
    cursor = c.find({}, {
        "_id": 0,
        "video_id": 1,
        "status": 1,
        "views": 1,
        "likes": 1,
        "comments": 1,
        "published_at": 1,
        "last_snapshot_ts": 1,
        "n_snapshots": 1,
        "horizons": 1
    })
    df = pd.DataFrame(list(cursor))
    return df

def coerce_datetime(df, cols):
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    return df

def basic_fix(df):
    if "status" in df.columns:
        df["status"] = df["status"].fillna("unknown")
    for c in ["views","likes","comments","n_snapshots"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = coerce_datetime(df, ["published_at", "last_snapshot_ts"])
    return df

def available_source():
    if os.path.exists("dashboard_summary.json"):
        return "JSON (snapshot)"
    if PYMONGO_OK:
        return "MongoDB (live)"
    return "JSON (snapshot)"

# ------------------------------
# Sidebar — source & filters
# ------------------------------
st.sidebar.header("Data Source")
default_src = available_source()
src = st.sidebar.radio("Select source:", ["JSON (snapshot)", "MongoDB (live)"],
                       index=["JSON (snapshot)", "MongoDB (live)"].index(default_src))

df = pd.DataFrame()
err = None

if src.startswith("JSON"):
    try:
        df = load_json("dashboard_summary.json")
    except Exception as e:
        err = f"JSON load error: {e}"
else:
    if not PYMONGO_OK:
        st.warning("PyMongo is not installed. Run:  pip install pymongo")
    try:
        MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        DB_NAME   = os.getenv("DB_NAME", "yt_autoscanner")
        COLL_NAME = os.getenv("COLL_NAME", "videos")
        df = load_mongo(MONGO_URI, DB_NAME, COLL_NAME)
    except Exception as e:
        err = f"Mongo error: {e}"

st.title("📊 YouTube Tracker — Local Dashboard")

if err:
    st.error(err)
    st.stop()

if df.empty:
    st.info("No data to display.")
    st.stop()

df = basic_fix(df)

# ==============================
# TOP: Highlights (cards)
# ==============================
col1, col2, col3, col4, col5 = st.columns(5)

total_videos = len(df)
complete_cnt = int((df["status"] == "complete").sum()) if "status" in df.columns else 0
pending_cnt  = total_videos - complete_cnt

avg_views = int(df["views"].mean()) if "views" in df.columns and df["views"].notna().any() else 0
max_views = int(df["views"].max()) if "views" in df.columns and df["views"].notna().any() else 0

col1.metric("Total Videos", f"{total_videos:,}")
col2.metric("Complete", f"{complete_cnt:,}")
col3.metric("Pending", f"{pending_cnt:,}")
col4.metric("Avg Views", f"{avg_views:,}")
col5.metric("Max Views", f"{max_views:,}")

st.caption("Tips: Pending = Total - Complete. Metrics refresh with filters below (except totals show pre-filter state).")

st.divider()

# ==============================
# FILTERS
# ==============================
with st.expander("🔍 Filters", expanded=True):
    c1, c2, c3, c4 = st.columns([1,1,1,1])

    # Status filter
    if "status" in df.columns:
        all_status = sorted(df["status"].dropna().unique().tolist())
        picked_status = c1.multiselect("Status", all_status, default=all_status)
    else:
        picked_status = None

    # Date range (published_at)
    if "published_at" in df.columns and df["published_at"].notna().any():
        min_d = pd.to_datetime(df["published_at"]).min().date()
        max_d = pd.to_datetime(df["published_at"]).max().date()
        date_range = c2.date_input("Published between (local time)", value=(min_d, max_d))
    else:
        date_range = None

    # Min views
    min_views = int(df["views"].min()) if "views" in df.columns and df["views"].notna().any() else 0
    max_views = int(df["views"].max()) if "views" in df.columns and df["views"].notna().any() else 0
    view_range = c3.slider("Views range", min_value=min_views, max_value=max_views, value=(min_views, max_views)) if max_views>0 else (0,0)

    # Search by video_id
    query_text = c4.text_input("Search `video_id` contains", "")

# Apply filters to create 'view'
view = df.copy()
if picked_status is not None and "status" in view.columns:
    view = view[view["status"].isin(picked_status)]

if date_range and isinstance(date_range, tuple) and len(date_range) == 2 and "published_at" in view.columns:
    start_d = pd.to_datetime(date_range[0])
    end_d   = pd.to_datetime(date_range[1]) + timedelta(days=1)
    view = view[(view["published_at"] >= start_d) & (view["published_at"] < end_d)]

if "views" in view.columns and max_views>0:
    vmin, vmax = view_range
    view = view[view["views"].between(vmin, vmax, inclusive="both")]

if query_text and "video_id" in view.columns:
    view = view[view["video_id"].astype(str).str.contains(query_text, case=False, na=False)]

# ==============================
# CHARTS (Plotly)
# ==============================
st.subheader("📈 Charts")

# A) Status distribution (bar or pie)
if "status" in view.columns and not view.empty:
    status_counts = view["status"].value_counts(dropna=False).reset_index()
    status_counts.columns = ["status", "count"]
    c1, c2 = st.columns(2)

    with c1:
        fig_bar = px.bar(status_counts, x="status", y="count", title="Videos by Status")
        fig_bar.update_layout(margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig_bar, use_container_width=True)

    with c2:
        fig_pie = px.pie(status_counts, names="status", values="count", title="Status Share")
        fig_pie.update_layout(margin=dict(l=10,r=10,t=40,b=10))
        st.plotly_chart(fig_pie, use_container_width=True)

# B) Daily video count by published date
if "published_at" in view.columns and view["published_at"].notna().any():
    tmp = view.copy()
    # Convert to local date (display purpose)
    tmp["published_local_date"] = tmp["published_at"].dt.tz_convert(None).dt.date
    daily = tmp.groupby("published_local_date", dropna=False).size().reset_index(name="count")
    fig_daily = px.line(daily, x="published_local_date", y="count", title="Daily New Videos (by Published Date)")
    fig_daily.update_layout(margin=dict(l=10,r=10,t=40,b=10), xaxis_title="Date", yaxis_title="Count")
    st.plotly_chart(fig_daily, use_container_width=True)

# C) Scatter: Published time vs Views
if {"published_at","views"}.issubset(view.columns) and view["published_at"].notna().any() and view["views"].notna().any():
    scatter_df = view.dropna(subset=["published_at","views"]).copy()
    scatter_df["published_local"] = scatter_df["published_at"].dt.tz_convert(None)
    fig_scatter = px.scatter(
        scatter_df, x="published_local", y="views", color=("status" if "status" in scatter_df.columns else None),
        title="Views vs Published Time", hover_data=["video_id"]
    )
    fig_scatter.update_layout(margin=dict(l=10,r=10,t=40,b=10), xaxis_title="Published (local)", yaxis_title="Views")
    st.plotly_chart(fig_scatter, use_container_width=True)

st.divider()

# ==============================
# DATA TABLE
# ==============================
st.subheader("📋 Table (Filtered)")
st.dataframe(view, use_container_width=True, height=420)

csv = view.to_csv(index=False).encode("utf-8")
st.download_button("⬇️ Download CSV", csv, file_name="dashboard_view.csv", mime="text/csv")

st.divider()

# ==============================
# VIDEO DETAILS + HORIZON CHART
# ==============================
st.subheader("📌 Video Details")
if "video_id" in view.columns and not view.empty:
    id_list = view["video_id"].astype(str).tolist()
    sel_id = st.selectbox("Pick a video_id", id_list, index=0)
    row = view[view["video_id"].astype(str) == str(sel_id)].head(1)

    if not row.empty:
        r = row.iloc[0].to_dict()
        cA, cB, cC, cD = st.columns(4)
        cA.metric("Views", f"{int(r.get('views', 0)):,}" if pd.notna(r.get('views')) else "—")
        cB.metric("Likes", f"{int(r.get('likes', 0)):,}" if pd.notna(r.get('likes')) else "—")
        cC.metric("Comments", f"{int(r.get('comments', 0)):,}" if pd.notna(r.get('comments')) else "—")
        cD.metric("Snapshots", f"{int(r.get('n_snapshots', 0)):,}" if pd.notna(r.get('n_snapshots')) else "—")

        st.write("**Status:**", r.get("status", "—"))
        st.write("**Published at (UTC):**", str(r.get("published_at", "—")))
        st.write("**Last snapshot (UTC):**", str(r.get("last_snapshot_ts", "—")))

        horizons = r.get("horizons", {})
        if isinstance(horizons, dict) and horizons:
            # Flatten horizons into a DataFrame
            rows = []
            for k, v in horizons.items():
                try:
                    h_min = int(k)  # minute horizon
                except:
                    continue
                rec = {"horizon_min": h_min}
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        rec[kk] = vv
                rows.append(rec)

            if rows:
                hdf = pd.DataFrame(rows).sort_values("horizon_min").reset_index(drop=True)
                st.write("**Horizon snapshots:**")
                st.dataframe(hdf, use_container_width=True)

                # Line chart for selected metrics across horizons
                metric_choices = [c for c in ["views","likes","comments"] if c in hdf.columns]
                if metric_choices:
                    picked_metrics = st.multiselect("Plot metrics over horizon (minutes)", metric_choices, default=metric_choices[:1])

                    if picked_metrics:
                        # reshape to long for Plotly
                        plot_long = hdf[["horizon_min"] + picked_metrics].melt(id_vars="horizon_min", var_name="metric", value_name="value")
                        fig_h = px.line(plot_long, x="horizon_min", y="value", color="metric",
                                        title=f"Metrics over Horizon — {sel_id}")
                        fig_h.update_layout(margin=dict(l=10,r=10,t=40,b=10), xaxis_title="Horizon (minutes)", yaxis_title="Value")
                        st.plotly_chart(fig_h, use_container_width=True)
                else:
                    st.info("No numeric metrics (views/likes/comments) available in horizons to plot.")
            else:
                st.info("No valid horizon records.")
        else:
            st.info("No horizons available for this video.")
else:
    st.info("No rows after filters or `video_id` column missing.")
