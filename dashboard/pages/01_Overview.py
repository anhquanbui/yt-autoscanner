import streamlit as st
import pandas as pd
from datetime import datetime, timezone
import sys
from pathlib import Path

# --------------------------------------------------
# Ensure project root is on sys.path so we can import
# local modules like config.db when running via Streamlit
# --------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]  # .../yt-autoscanner
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.db import get_db, _resolve_db_name


# =============== WORKER DEFINITIONS ===============
# Bản local: chỉ cần theo dõi last_run của các worker,
# không cần biết service systemd hay Start/Stop gì cả.
#
# Format:
#   key   = name stored in Mongo.worker_runs.name
#   label = human-readable label on the dashboard
WORKERS = [
    ("discover_once", "Discover new videos"),
    ("track_once", "Track stats & snapshots"),
    ("low_quality_autoflag_3h", "Low-quality scoring (3h)"),
    ("low_quality_autoflag_6h", "Low-quality scoring (6h)"),
    ("compute_dashboard_kpis", "Dashboard KPI snapshot"),
    ("viral_scoring_h6", "Viral scoring 6h"),
    ("viral_scoring_h12", "Viral scoring 12h"),
    ("viral_scoring_h24", "Viral scoring 24h"),
    ("viral_finalize", "Viral finalize (≥24h)"),
]


# =============== GLOBAL LAYOUT & THEME ===============
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] {
    background: #f3f4f6;
}

.main .block-container {
    max-width: 1200px;
    padding-top: 2.2rem;
    padding-left: 2.5rem;
    padding-right: 2.5rem;
    margin-left: auto;
    margin-right: auto;
}

h1 {
    font-weight: 700 !important;
    letter-spacing: -0.02em;
}
h2, h3 {
    font-weight: 600 !important;
}

.block-container hr {
    margin-top: 1.7rem;
    margin-bottom: 1.7rem;
}

/* Small caption tweak */
.block-container p, .block-container .stMarkdown {
    font-size: 0.95rem;
}

/* Worker table styling */
.worker-row {
    padding: 8px 12px;
    border-bottom: 1px solid #e5e7eb;
}
.worker-header {
    padding: 6px 12px;
    border-bottom: 1px solid #d1d5db;
    background: #f9fafb;
    font-weight: 600;
    font-size: 0.9rem;
    color: #374151;
}
</style>
    """,
    unsafe_allow_html=True,
)


# =============== SMALL HELPERS ===============

def render_health_badge(state: str) -> str:
    """
    Render a small colored badge for worker health state.

    state in {"healthy", "warning", "stopped"}
    """
    s = (state or "").lower()
    if s == "healthy":
        color = "#22c55e"
        text = "Healthy"
    elif s == "warning":
        color = "#facc15"
        text = "Warning"
    else:
        color = "#9ca3af"
        text = "Stopped"

    return f"""
<span style="
  display:inline-flex;
  align-items:center;
  padding:2px 10px;
  border-radius:999px;
  background:{color}1a;
  border:1px solid {color};
  font-size:0.8rem;
  color:#111827;
">
  <span style="width:8px;height:8px;border-radius:999px;background:{color};margin-right:6px;"></span>
  {text}
</span>
"""


def classify_worker_doc(doc, now: datetime, stale_sec: int = 3 * 3600) -> str:
    """
    Classify a worker's health from its worker_runs document.

    - "stopped" : no last_run at all
    - "warning" : last_run > stale_sec OR status is error-like
    - "healthy" : otherwise
    """
    if not doc or "last_run" not in doc:
        return "stopped"

    ts = doc["last_run"]
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
    else:
        age = 99999999  # effectively "very stale"

    status_raw = str(doc.get("status", "")).lower()
    has_error = (
        status_raw.startswith("err")
        or status_raw in {"error", "failed", "fatal", "stopped"}
    )
    is_stale = age > stale_sec

    if has_error or is_stale:
        return "warning"
    return "healthy"


# =============== DATA LOADERS ===============

@st.cache_data(ttl=10)
def load_kpis() -> dict:
    """
    Load the latest dashboard KPIs from Mongo.

    - Reads the most recent document from `dashboard_kpis`.
    - Fills any missing fields with defaults from `base`.
    - Normalizes numbers to int and formats snapshot timestamp.

    Cache TTL = 10 seconds to avoid hammering Mongo on every rerender.
    """
    db = get_db()
    doc = db.dashboard_kpis.find_one(sort=[("ts", -1)])

    base = {
        "total_videos": 0,
        "total_channels": 0,
        "tracking_active": 0,
        "completed_total": 0,
        "stopped_total": 0,
        "completed_age24": 0,
        "completed_removed": 0,
        "stopped_low_quality": 0,
        "low_quality_flagged": 0,
        "snapshot_ts": None,

        # Viral v2 metrics (6h / 12h / 24h + final)
        "viral2_h6_scored": 0,
        "viral2_h6_candidates": 0,
        "viral2_h12_scored": 0,
        "viral2_12h_viral": 0,
        "viral2_h24_scored": 0,
        "viral2_final_viral": 0,
        "viral2_final_nonviral": 0,
        "viral2_final_nonviral_lowq": 0,
        "viral2_final_unknown": 0,
        "viral2_final_decided": 0,
    }

    if not doc:
        return base

    for key in base:
        if key == "snapshot_ts":
            continue
        try:
            base[key] = int(doc.get(key, 0))
        except Exception:
            base[key] = 0

    ts = doc.get("ts")
    if isinstance(ts, datetime):
        ts = ts.astimezone(timezone.utc)
        base["snapshot_ts"] = ts.strftime("%Y-%m-%d %H:%M UTC")
    elif ts:
        base["snapshot_ts"] = str(ts)

    return base


@st.cache_data(ttl=60)
def load_worker_last_runs():
    """
    Load last_run timestamps + per-worker health from Mongo.worker_runs.

    Returns
    -------
    List[dict]
        Each dict has:
        - "Key"      = worker key
        - "Worker"   = human-readable name
        - "Last run" = relative time string (e.g. "3m ago", "2h ago") or "No data".
        - "Health"   = "healthy" / "warning" / "stopped"
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    rows = []

    for key, label in WORKERS:
        doc = db.worker_runs.find_one({"name": key}, sort=[("last_run", -1)])

        if not doc or "last_run" not in doc:
            rows.append(
                {
                    "Key": key,
                    "Worker": label,
                    "Last run": "No data",
                    "Health": "stopped",
                }
            )
            continue

        ts = doc["last_run"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            diff = now - ts
            sec = diff.total_seconds()

            if sec < 60:
                pretty = f"{int(sec)}s ago"
            elif sec < 3600:
                pretty = f"{int(sec // 60)}m ago"
            elif sec < 86400:
                pretty = f"{int(sec // 3600)}h ago"
            else:
                pretty = f"{int(sec // 86400)}d ago"
        else:
            pretty = str(ts)

        health = classify_worker_doc(doc, now)

        rows.append(
            {"Key": key, "Worker": label, "Last run": pretty, "Health": health}
        )

    return rows


@st.cache_data(ttl=60)
def load_worker_health():
    """
    Compute a high-level health summary for all defined workers.

    A worker is considered:
    - "stopped"  if there is no last_run at all.
    - "warning"  if last_run is stale (> 3h) OR status in Mongo indicates error.
    - "healthy"  otherwise.
    """
    db = get_db()
    core_workers = [w[0] for w in WORKERS]

    now = datetime.now(timezone.utc)
    summary = {"total": len(core_workers), "healthy": 0, "warning": 0, "stopped": 0}

    for w in core_workers:
        doc = db.worker_runs.find_one({"name": w}, sort=[("last_run", -1)])
        state = classify_worker_doc(doc, now)

        if state == "healthy":
            summary["healthy"] += 1
        elif state == "warning":
            summary["warning"] += 1
        else:
            summary["stopped"] += 1

    return summary


# =============== METRIC CARDS ===============

def render_simple_metrics(kpis: dict):
    total_videos = kpis.get("total_videos", 0)

    def pct_value(v):
        return (v / total_videos * 100.0) if total_videos else 0.0

    def pct_str(v):
        return f"{pct_value(v):.2f}%"

    st.markdown(
        """
<style>
.metric-card {
    background: #ffffff;
    padding: 18px 22px;
    border-radius: 14px;
    border: 1px solid #e5e7eb;
    box-shadow: 0 3px 8px rgba(15,23,42,0.05);
    transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.metric-card:hover {
    box-shadow: 0 6px 16px rgba(15,23,42,0.12);
    transform: translateY(-1px);
}
.metric-title {
    font-size: 0.86rem;
    font-weight: 600;
    color: #6b7280;
}
.metric-value {
    font-size: 1.45rem;
    font-weight: 700;
    color: #111827;
}
.metric-progress-outer {
    margin-top: 6px;
    width: 100%;
    height: 6px;
    border-radius: 999px;
    background: #e5e7eb;
}
.metric-progress-inner {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, #4f46e5, #3b82f6);
}
.metric-percentage {
    font-size: 0.9rem;
    color: #6b7280;
    margin-top: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)

    cards = [
        ("Tracking active", kpis["tracking_active"]),
        ("Completed (24h reached)", kpis["completed_age24"]),
        ("Removed / Unavailable", kpis["completed_removed"]),
        ("Stopped (low quality)", kpis["stopped_low_quality"]),
    ]

    for col, (title, value) in zip([c1, c2, c3, c4], cards):
        col.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-title">{title}</div>
                <div class="metric-value">{value:,}</div>
                <div class="metric-progress-outer">
                    <div class="metric-progress-inner" style="width:{pct_value(value):.2f}%"></div>
                </div>
                <div class="metric-percentage">{pct_str(value)} of all videos</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_viral_metrics(kpis: dict):
    total_videos = kpis.get("total_videos", 0)

    def pct_value(v):
        return (v / total_videos * 100.0) if total_videos else 0.0

    def pct_str(v):
        return f"{pct_value(v):.2f}% of total videos"

    viral_likely = kpis.get("viral2_h6_candidates", 0)
    viral_confirmed = kpis.get("viral2_12h_viral", 0)

    final_decided = kpis.get("viral2_final_decided", 0)
    final_viral = kpis.get("viral2_final_viral", 0)
    final_non = kpis.get("viral2_final_nonviral", 0) + kpis.get(
        "viral2_final_nonviral_lowq", 0
    )
    final_unknown = kpis.get("viral2_final_unknown", 0)

    c1, c2, c3 = st.columns(3)

    c1.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">🔥 Viral Likely (6h)</div>
            <div class="metric-value">{viral_likely:,}</div>
            <div class="metric-progress-outer">
                <div class="metric-progress-inner" style="width:{pct_value(viral_likely):.2f}%"></div>
            </div>
            <div class="metric-percentage">{pct_str(viral_likely)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c2.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">🔥 Viral Confirmed (12h)</div>
            <div class="metric-value">{viral_confirmed:,}</div>
            <div class="metric-progress-outer">
                <div class="metric-progress-inner" style="width:{pct_value(viral_confirmed):.2f}%"></div>
            </div>
            <div class="metric-percentage">{pct_str(viral_confirmed)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    breakdown = f"Viral {final_viral:,} • Non {final_non:,} • Unk {final_unknown:,}"

    c3.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">🏁 Finalized (Viral / Non / Unk)</div>
            <div class="metric-value">{final_decided:,}</div>
            <div class="metric-progress-outer">
                <div class="metric-progress-inner" style="width:{pct_value(final_decided):.2f}%"></div>
            </div>
            <div class="metric-percentage">{pct_str(final_decided)}<br/>{breakdown}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============== SYSTEM STATUS ===============

def compute_system_status(kpis: dict, wh: dict):
    total_videos = kpis.get("total_videos", 0)

    if total_videos == 0:
        return ("Idle", "No videos discovered yet", "#9ca3af")

    healthy = wh["healthy"]
    warning = wh["warning"]
    stopped = wh["stopped"]

    if healthy == 0 and (warning + stopped) > 0:
        return ("Stopped", "All workers appear stopped or stale", "#ef4444")

    if (warning + stopped) > 0:
        return ("Warning", f"{warning + stopped} worker(s) have issues", "#facc15")

    return ("Healthy", "All core workers running normally", "#22c55e")


# =============== PAGE BODY ===============

st.title("📊 YouTube AutoScanner — Overview (Local)")
st.subheader("📌 System KPIs")

if st.button("🔄 Refresh now"):
    load_kpis.clear()
    load_worker_last_runs.clear()
    load_worker_health.clear()
    st.experimental_rerun()

kpis = load_kpis()

if kpis.get("snapshot_ts"):
    st.caption(f"Last KPI snapshot: {kpis['snapshot_ts']}")

c1, c2 = st.columns(2)
c1.metric("Total Videos", f"{kpis['total_videos']:,}")
c2.metric("Total Channels", f"{kpis['total_channels']:,}")

st.markdown("---")

st.markdown("### 🎯 Tracking & Completion Overview")
render_simple_metrics(kpis)

st.markdown("<div style='margin-top:1.4rem'></div>", unsafe_allow_html=True)

st.markdown("### 📈 Tracking Progress")
tracking_total = kpis["tracking_active"] + kpis["completed_total"] + kpis["stopped_total"]
finished = kpis["completed_total"] + kpis["stopped_total"]

if tracking_total > 0:
    ratio = finished / tracking_total
    st.progress(ratio, text=f"{finished:,} / {tracking_total:,} videos (~{ratio*100:.1f}%)")
else:
    st.info("No videos in tracking pipeline.")

st.markdown("<div style='margin-top:1.8rem'></div>", unsafe_allow_html=True)
st.markdown("### 🔥 Viral ML Models (6h / 12h / 24h / Final)")
render_viral_metrics(kpis)

st.markdown("<div style='margin-top:1.8rem'></div>", unsafe_allow_html=True)

st.markdown("### 💡 System Status & Worker Activity")

worker_rows = load_worker_last_runs()
worker_health = load_worker_health()

label, desc, color = compute_system_status(kpis, worker_health)

st.markdown(
    f"""
<div style="
  display:flex;align-items:center;
  padding:6px 12px;border-radius:999px;
  background:{color}1A;border:1px solid {color};
  margin-bottom:0.7rem;">
  <span style="width:10px;height:10px;border-radius:999px;background:{color};margin-right:8px;"></span>
  <span style="font-weight:600;color:#111827;margin-right:6px;">{label}</span>
  <span style="font-size:0.86rem;color:#4b5563;">{desc}</span>
</div>
""",
    unsafe_allow_html=True,
)

st.markdown("#### Worker last runs (local PowerShell loop)")

# Header row: Worker / Last run / Health
h_cols = st.columns([3, 2, 2])
h_cols[0].markdown('<div class="worker-header">Worker</div>', unsafe_allow_html=True)
h_cols[1].markdown('<div class="worker-header">Last run</div>', unsafe_allow_html=True)
h_cols[2].markdown('<div class="worker-header">Health</div>', unsafe_allow_html=True)

for row in worker_rows:
    c0, c1, c2 = st.columns([3, 2, 2])

    with c0:
        st.markdown(
            f'<div class="worker-row"><strong>{row["Worker"]}</strong></div>',
            unsafe_allow_html=True,
        )

    with c1:
        st.markdown(
            f'<div class="worker-row">{row["Last run"]}</div>',
            unsafe_allow_html=True,
        )

    with c2:
        badge = render_health_badge(row["Health"])
        st.markdown(f'<div class="worker-row">{badge}</div>', unsafe_allow_html=True)

st.markdown("---")
st.caption("YouTube AutoScanner — Overview Dashboard (Local dev)")
