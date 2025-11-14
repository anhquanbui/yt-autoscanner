# dashboard/pages/01_Overview.py

import streamlit as st
import pandas as pd
import plotly.express as px  # vẫn dùng cho pie chart

from components.db import get_db


# =============== DATA LOADERS ===============

@st.cache_data(ttl=60)
def load_kpis() -> dict:
    """Aggregate all high-level KPIs in a single Mongo pipeline."""
    db = get_db()

    pipeline = [
        {
            "$group": {
                "_id": None,

                # total counts
                "total_videos": {"$sum": 1},
                "total_channels_set": {"$addToSet": "$snippet.channelId"},

                # tracking status
                "tracking_active": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$tracking.status", "tracking"]},
                            1,
                            0,
                        ]
                    }
                },
                "completed_total": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$tracking.status", "complete"]},
                            1,
                            0,
                        ]
                    }
                },
                "stopped_total": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$tracking.status", "stopped"]},
                            1,
                            0,
                        ]
                    }
                },

                # completed by stop_reason
                "completed_age24": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "complete"]},
                                    {"$eq": ["$tracking.stop_reason", "age>=24h"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "completed_removed": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "complete"]},
                                    {
                                        "$in": [
                                            "$tracking.stop_reason",
                                            ["removed", "deleted", "not_found"],
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # stopped by low-quality filter
                "stopped_low_quality": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "stopped"]},
                                    {
                                        "$in": [
                                            "$tracking.stop_reason",
                                            ["low_quality", "ml_low_quality"],
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # ML flags (optional)
                "low_quality_flagged": {
                    "$sum": {
                        "$cond": [
                            {
                                "$or": [
                                    {"$eq": ["$ml_flags.low_quality", 1]},
                                    {"$eq": ["$ml_flags.low_quality", True]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "total_videos": 1,
                "total_channels": {"$size": "$total_channels_set"},
                "tracking_active": 1,
                "completed_total": 1,
                "stopped_total": 1,
                "completed_age24": 1,
                "completed_removed": 1,
                "stopped_low_quality": 1,
                "low_quality_flagged": 1,
            }
        },
    ]

    db_result = list(db.videos.aggregate(pipeline))
    if not db_result:
        return {
            "total_videos": 0,
            "total_channels": 0,
            "tracking_active": 0,
            "completed_total": 0,
            "stopped_total": 0,
            "completed_age24": 0,
            "completed_removed": 0,
            "stopped_low_quality": 0,
            "low_quality_flagged": 0,
        }

    return db_result[0]


# =============== STYLE RENDERERS ===============

def render_style_1(kpis: dict):
    """Simple metrics using st.metric."""
    c1, c2, c3 = st.columns(3)
    c1.metric("Tracking active", f"{kpis['tracking_active']:,}")
    c2.metric("Completed", f"{kpis['completed_total']:,}")
    c3.metric("Stopped (low quality)", f"{kpis['stopped_low_quality']:,}")


def render_style_2(kpis: dict):
    """Gradient cards style."""
    st.markdown(
        """
<style>
.grad-card {
    border-radius: 14px;
    padding: 16px 18px;
    color: #ffffff;
    box-shadow: 0 10px 25px rgba(15, 23, 42, 0.25);
}
.grad-1 {
    background: linear-gradient(135deg, #2563eb, #22c55e);
}
.grad-2 {
    background: linear-gradient(135deg, #22c55e, #facc15);
}
.grad-3 {
    background: linear-gradient(135deg, #ef4444, #ec4899);
}
.grad-title {
    font-size: 0.9rem;
    font-weight: 600;
    opacity: 0.9;
}
.grad-value {
    margin-top: 6px;
    font-size: 2.3rem;
    font-weight: 650;
}
.grad-sub {
    font-size: 0.8rem;
    opacity: 0.9;
    margin-top: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f"""
            <div class="grad-card grad-1">
                <div class="grad-title">Tracking Active</div>
                <div class="grad-value">{kpis['tracking_active']:,}</div>
                <div class="grad-sub">Videos currently being polled</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="grad-card grad-2">
                <div class="grad-title">Completed</div>
                <div class="grad-value">{kpis['completed_total']:,}</div>
                <div class="grad-sub">Finished their 24h tracking lifecycle</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="grad-card grad-3">
                <div class="grad-title">Stopped (Low Quality)</div>
                <div class="grad-value">{kpis['stopped_low_quality']:,}</div>
                <div class="grad-sub">Stopped early by low-quality filter</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_style_3_glass(kpis: dict):
    """Glassmorphism cards (Style 3)."""
    st.markdown(
        """
<style>
.glass-card {
    background: rgba(255, 255, 255, 0.12);
    border-radius: 18px;
    padding: 16px 20px;
    border: 1px solid rgba(255,255,255,0.4);
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.18);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
}
.glass-title {
    font-weight: 600;
    font-size: 0.95rem;
    color: #111827;
    margin-bottom: 4px;
}
.glass-value {
    font-size: 2.3rem;
    font-weight: 650;
    margin: 0;
    color: #111827;
}
.glass-sub {
    font-size: 0.8rem;
    color: #6b7280;
    margin-top: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">🔄 Tracking Active</div>
                <p class="glass-value">{kpis['tracking_active']:,}</p>
                <div class="glass-sub">Videos currently being polled</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">✅ Completed</div>
                <p class="glass-value">{kpis['completed_total']:,}</p>
                <div class="glass-sub">Finished their 24h tracking lifecycle</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="glass-card">
                <div class="glass-title">⛔ Stopped (Low Quality)</div>
                <p class="glass-value">{kpis['stopped_low_quality']:,}</p>
                <div class="glass-sub">Stopped early by low-quality filter</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =============== PAGE BODY ===============

st.title("📊 YouTube AutoScanner — Overview")

st.subheader("📌 System KPIs")

try:
    kpis = load_kpis()
except Exception as e:
    st.error(f"❌ Error loading KPIs: {e}")
    st.stop()

# Top-level basic KPIs
top1, top2, top3 = st.columns(3)
top1.metric("Total Videos", f"{kpis['total_videos']:,}")
top2.metric("Total Channels", f"{kpis['total_channels']:,}")
top3.metric("Low-Quality Flagged (ML)", f"{kpis['low_quality_flagged']:,}")

st.markdown("---")

# === Tracking & completion with switchable style (in sidebar settings) ===

with st.sidebar:
    st.markdown("#### ⚙️ Overview page settings")
    style_choice = st.radio(
        "Card style",
        ["Style 1 – Simple", "Style 2 – Gradient", "Style 3 – Glass"],
        index=2,  # mặc định chọn Glass
    )

st.markdown("### 🎯 Tracking & Completion Overview")

if style_choice.startswith("Style 1"):
    render_style_1(kpis)
elif style_choice.startswith("Style 2"):
    render_style_2(kpis)
else:
    render_style_3_glass(kpis)

st.markdown("<div style='margin-top: 1.5rem;'></div>", unsafe_allow_html=True)

# === Completed breakdown (donut chart) ===
st.markdown("### 🥧 Completed Breakdown")

completed_total = kpis["completed_age24"] + kpis["completed_removed"]
completed_df = pd.DataFrame(
    {
        "Reason": ["Natural (24h reached)", "Removed / Unavailable"],
        "Count": [kpis["completed_age24"], kpis["completed_removed"]],
    }
)

if completed_total == 0:
    st.info("No completed videos yet — keep tracking to see the breakdown.")
else:
    fig_completed = px.pie(
        completed_df,
        names="Reason",
        values="Count",
        hole=0.4,
        title="Distribution of completed videos by stop reason",
    )
    fig_completed.update_traces(
        textinfo="value+percent",
        hovertemplate="%{label}<br>%{value} videos<br>%{percent}",
    )
    st.plotly_chart(fig_completed, use_container_width=True)

st.markdown("---")

st.caption("YouTube AutoScanner — Overview Dashboard")
