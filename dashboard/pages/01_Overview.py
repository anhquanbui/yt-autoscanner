# dashboard/pages/01_Overview.py

import streamlit as st
import pandas as pd
import plotly.express as px

from components.db import get_db


# =============== GLOBAL LAYOUT FIX ===============
# Khóa lại block-container để tránh cảm giác "thụt vào" khi F5, nhất là với Theme Style 3
st.markdown(
    """
<style>
.main .block-container {
    max-width: 1200px;
    padding-top: 2rem;
    padding-left: 2.5rem;
    padding-right: 2.5rem;
    margin-left: auto;
    margin-right: auto;
}
</style>
    """,
    unsafe_allow_html=True,
)


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

                # stopped by low-quality filter (bất kỳ stop_reason chứa "low_quality")
                "stopped_low_quality": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$eq": ["$tracking.status", "stopped"]},
                                    {
                                        "$gt": [
                                            {
                                                "$indexOfBytes": [
                                                    "$tracking.stop_reason",
                                                    "low_quality",
                                                ]
                                            },
                                            -1,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },

                # ML flags (optional – hiện không dùng ở UI)
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
    """Glassmorphism cards (Theme Style 3)."""
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
top1, top2 = st.columns(2)
top1.metric("Total Videos", f"{kpis['total_videos']:,}")
top2.metric("Total Channels", f"{kpis['total_channels']:,}")

st.markdown("---")

# === Overview page settings in sidebar ===
with st.sidebar:
    st.markdown("#### ⚙️ Overview page settings")
    style_choice = st.radio(
        "Theme style",
        ["Theme Style 1", "Theme Style 2", "Theme Style 3"],
        index=1,  # mặc định chọn Theme Style 3 (Glass)
    )

st.markdown("### 🎯 Tracking & Completion Overview")

if style_choice.startswith("Theme Style 1"):
    render_style_1(kpis)
elif style_choice.startswith("Theme Style 2"):
    render_style_2(kpis)
else:
    render_style_3_glass(kpis)

st.markdown("<div style='margin-top: 1.5rem;'></div>", unsafe_allow_html=True)

# === Outcome breakdown (donut chart) ===
st.markdown("### 🥧 Outcome Breakdown (24h window)")

outcome_df_full = pd.DataFrame(
    {
        "Reason": [
            "Natural (24h reached)",
            "Removed / Unavailable",
            "Stopped early (Low quality)",
        ],
        "Count": [
            kpis["completed_age24"],
            kpis["completed_removed"],
            kpis["stopped_low_quality"],
        ],
    }
)

outcome_df_pie = outcome_df_full[outcome_df_full["Count"] > 0]
outcome_total = int(outcome_df_full["Count"].sum()) if not outcome_df_full.empty else 0

if outcome_total == 0 or outcome_df_pie.empty:
    st.info("No completed or stopped videos yet — keep tracking to see the breakdown.")
else:
    # Theme của pie chart tự động theo Theme Style
    if style_choice.startswith("Theme Style 1"):
        template = "plotly_white"
        colors = px.colors.sequential.Blues
    elif style_choice.startswith("Theme Style 2"):
        template = "plotly_white"
        colors = ["#2563eb", "#22c55e", "#facc15", "#ef4444"]
    else:  # Theme Style 3 (Glass) – pastel nhẹ
        template = "plotly_white"
        colors = px.colors.qualitative.Pastel

    fig_completed = px.pie(
        outcome_df_pie,
        names="Reason",
        values="Count",
        hole=0.4,
        title="Distribution of outcomes by stop reason",
        color_discrete_sequence=colors,
    )
    fig_completed.update_layout(template=template)
    fig_completed.update_traces(
        textinfo="value+percent",
        hovertemplate="%{label}<br>%{value} videos<br>%{percent}",
    )
    st.plotly_chart(fig_completed, use_container_width=True)

# Summary bên dưới: luôn hiển thị đủ 3 loại, kể cả = 0
st.markdown("#### Outcome summary")
for _, row in outcome_df_full.iterrows():
    st.markdown(
        f"- **{row['Reason']}**: `{int(row['Count']):,}` video(s)"
    )

st.markdown("---")

st.caption("YouTube AutoScanner — Overview Dashboard")
