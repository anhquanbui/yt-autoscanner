import streamlit as st
from datetime import datetime, timezone

from config.db import get_db


# ============================================================
# WORKER DEFINITIONS
# ============================================================
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


# ============================================================
# BADGE RENDERER
# ============================================================
def render_health_badge(state: str) -> str:
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


# ============================================================
# CLASSIFY WORKER HEALTH
# ============================================================
def classify_worker_doc(doc, now: datetime, stale_sec: int = 3 * 3600) -> str:
    if not doc or "last_run" not in doc:
        return "stopped"

    ts = doc["last_run"]
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
    else:
        age = 99999999

    status_raw = str(doc.get("status", "")).lower()
    has_error = (
        status_raw.startswith("err")
        or status_raw in {"error", "failed", "fatal", "stopped"}
    )
    is_stale = age > stale_sec

    if has_error or is_stale:
        return "warning"
    return "healthy"


# ============================================================
# LOAD WORKER LAST RUNS
# ============================================================
@st.cache_data(ttl=60)
def load_worker_last_runs():
    db = get_db()
    now = datetime.now(timezone.utc)
    rows = []

    for key, label in WORKERS:
        doc = db.worker_runs.find_one({"name": key}, sort=[("last_run", -1)])

        if not doc or "last_run" not in doc:
            rows.append({
                "Key": key,
                "Worker": label,
                "Last run": "No data",
                "Health": "stopped",
            })
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


# ============================================================
# SUMMARY HEALTH
# ============================================================
@st.cache_data(ttl=60)
def load_worker_health():
    db = get_db()
    now = datetime.now(timezone.utc)

    summary = {"total": len(WORKERS), "healthy": 0, "warning": 0, "stopped": 0}

    for key, _ in WORKERS:
        doc = db.worker_runs.find_one({"name": key}, sort=[("last_run", -1)])
        state = classify_worker_doc(doc, now)

        if state == "healthy":
            summary["healthy"] += 1
        elif state == "warning":
            summary["warning"] += 1
        else:
            summary["stopped"] += 1

    return summary


# ============================================================
# COMPUTE SYSTEM STATUS
# ============================================================
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


# ============================================================
# RENDER UI BLOCK
# ============================================================
def render_system_status(kpis: dict):
    rows = load_worker_last_runs()
    wh = load_worker_health()

    label, desc, color = compute_system_status(kpis, wh)

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

    st.markdown("#### Worker last runs")

    # Header row
    h_cols = st.columns([3, 2, 2])
    h_cols[0].markdown("**Worker**")
    h_cols[1].markdown("**Last run**")
    h_cols[2].markdown("**Health**")

    # Rows
    for row in rows:
        c0, c1, c2 = st.columns([3, 2, 2])

        with c0:
            st.markdown(f"**{row['Worker']}**")

        with c1:
            st.markdown(row["Last run"])

        with c2:
            st.markdown(render_health_badge(row["Health"]), unsafe_allow_html=True)
