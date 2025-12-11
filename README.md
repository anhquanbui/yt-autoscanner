
# 🎯 YouTube AutoScanner — VPS Deployment Edition (2025)

This repository powers a full 24/7 automation pipeline for discovering, tracking, and evaluating YouTube videos during their first 24 hours. It is designed for **VPS deployment**, using `systemd`, `tmux`, MongoDB, and Python workers.

---

## 📦 Project Structure (2025 Updated)

```bash
yt-autoscanner/
│
├── .venv/                         # Python virtual environment
│
├── api/
│   ├── main.py
│   └── config/
│       └── path_utils.py
│
├── app.py                         # Root Streamlit entrypoint (dashboard home)
│
├── config/
│   ├── __init__.py
│   ├── db.py                      # MongoDB helpers
│   ├── env.py                     # .env loader
│   └── path_utils.py
│
├── dashboard/
│   ├── components/                # Shared UI components
│   └── pages/
│       ├── 01_Overview.py
│       └── 02_videos.py
│
├── logs/
│
├── models/
│   │── low_quality/
│   │    ├── low_quality_model_3h.joblib
│   │    └── low_quality_model_6h.joblib
│   └── viral/
│        ├── viral_xgb_6h.joblib
│        ├── viral_xgb_12h.joblib
│        └── viral_xgb_24h.joblib
│
├── scripts/
│   ├── auto_discover.sh
│   ├── auto_kpis.sh
│   ├── auto_lowq_3h.sh
│   ├── auto_lowq_6h.sh
│   ├── auto_quality.sh
│   ├── auto_track.sh
│   ├── auto_viral_finalize.sh
│   ├── auto_viral.sh
│   ├── mongo_backup.sh
│   └── mongo_backup-full.sh
│
├── systemd/
│   ├── yt-auto-discover.service
│   ├── yt-auto-track.service
│   ├── yt-dashboard.service
│   ├── yt-kpis.service
│   ├── yt-lowq-3h.service
│   ├── yt-lowq-6h.service
│   ├── yt-viral-finalize.service
│   └── yt-viral.service
│
├── tools/
│   ├── make_indexes.py
│   ├── backfill_channels.py
│   ├── backfill_missing_fields.py
│   └── ml_flags_migrate.py
│
├── worker/
│   ├── __init__.py
│   ├── compute_dashboard_kpis.py
│   ├── discover_once.py
│   ├── low_quality_3h_worker.py
│   ├── low_quality_6h_worker.py
│   ├── low_quality_core.py
│   ├── process_data.py
│   └── track_once.py
│
├── README.md
├── requirements-dev.txt
└── .gitignore
```

---

## 🔧 Key Components

### **Discover Worker — `worker/discover_once.py`**
Fetches YouTube trending/search results, inserts videos into DB, sets first tracking timestamp.

### **Track Worker — `worker/track_once.py`**
Tracks video performance from 0 → 24h and stores snapshots.

### **ML Workers — `low_quality_3h_worker.py` & `low_quality_6h_worker.py`**
Runs ML models at 3h and 6h to automatically detect low-quality videos during early engagement.

### **ML Worker — `viral_prediction_core.py` (6h / 12h / 24h)**
Runs the Viral Prediction model at three key time milestones:
- **6h** — early signal scoring  
- **12h** — viral confirmation  
- **24h** — viral validation + preparing for final decision  

### **KPI Worker — `compute_dashboard_kpis.py`**
Writes:

- `dashboard_kpis_overview`
- `worker_status`

### **Data Processor — `process_data.py`**
Cleans → aggregates → exports as Parquet.

---

## 🔑 Environment Variables

Stored in:

```
/home/ytscan/.env
```

Example:

```env
YT_API_KEY="AIzaSyAOZ8mMUHKNRjYDUMsQEVUatFMVUl717Ws"

# ==== MongoDB credentials & target DB ====
MONGO_URI="connection_string"

MONGO_USER="your_user"
MONGO_PASS="your_password"
DB_NAME="ytscan"

# For worker / tools
MONGO_DB="ytscan"

# Main Collection
MONGO_VIDEOS_COLLECTION="videos"
MONGO_COLL="videos"

# ==== Docker Mongo container name ====
MONGO_CONTAINER="ytscan-mongo"

# ==== Local backup folder ====
BACKUP_DIR="/home/ytscan/mongo_backups"

# ==== Rclone remote (optional) ====
RCLONE_REMOTE_NAME="gdrive"
RCLONE_REMOTE_DIR="ytscan-mongo-backups"

# ==== Mongo backup ====
JSON_SKIP_COLLECTIONS=""
JSON_SAMPLE_COLLECTIONS="videos,channels"
JSON_SAMPLE_LIMIT=5000


# =============================
# === Machine Learning Flags ===
# === Dual-model (3h + 6h)  ===
# =============================

# --------- 3H MODEL ----------
LOWQ_MODEL_3H_PATH="models/low_quality/low_quality_model_3h.joblib"
LOWQ_THRESHOLD_3H=0.4
LOWQ_3H_ENABLED=true
LOWQ_3H_ONLY_MISSING=true
LOWQ_3H_STOP_IF_LOW=true

# --------- 6H MODEL ----------
LOWQ_MODEL_6H_PATH="models/low_quality/low_quality_model_6h.joblib"
LOWQ_THRESHOLD_6H=0.33
LOWQ_6H_ENABLED=true
LOWQ_6H_ONLY_MISSING=true
LOWQ_6H_STOP_IF_LOW=true


# =============================
# ==== Viral Prediction ML ====
# =============================

VIRAL_MODEL_DIR="/home/ytscan/yt-autoscanner/models/viral"


# =============================
# ==== Dashboard Login (if available) ====
# =============================
DASHBOARD_USER="admin"
DASHBOARD_PASSWORD="abcd1234"
```

---

## 📚 MongoDB Collections

| Collection             | Purpose                                 |
|------------------------|-------------------------------------------|
| videos                 | Main ingestion + tracking (raw + stats)   |
| processed_videos       | Cleaned / enriched dataset (optional)     |
| channels               | Channel metadata                          |
| dashboard_kpis         | Cached KPIs for dashboard Overview        |
| worker_runs            | History of worker executions (timestamps) |

## 🧠 Video Schema (2025)

### Core

```
video_id
channelId
title
region_code
duration_seconds
publishedAt
```

### Tracking

```
tracking.status
tracking.stop_reason
tracking.started_at
tracking.next_poll_after
stats_snapshots[]
```

### ML Flags

```
## ML Flags Structure (ml_flags)

ml_flags:
  viral_v2:
    model_version: <int>
    label_rule_version: <int>

    h6:
      score_proba: <float|null>
      score_100: <int|null>
      is_candidate: <bool|null>
      threshold_proba: <float>        # e.g. 0.6
      threshold_100: <float|int>      # e.g. 60
      evaluated_at: <timestamp|null>

    h12:
      score_proba: <float|null>
      score_100: <int|null>
      is_viral_12h: <bool|null>
      threshold_proba: <float>        # e.g. 0.7
      threshold_100: <float|int>      # e.g. 70
      evaluated_at: <timestamp|null>

    h24_validation:
      score_proba: <float|null>
      score_100: <int|null>
      evaluated_at: <timestamp|null>

    final:
      status: "viral" | "non_viral" | "non_viral_lowq" | "unknown"
      decided_stage: 6h | 12h | 24h | null
      score_proba: <float|null>
      score_100: <int|null>
      threshold_proba: <float|null>
      threshold_100: <float|null>
      decided_at: <timestamp|null>
      reason: <string|null>

  low_quality_v1_3h:
    is_low: <bool>
    score: <float>
    threshold: <float|null>
    updated_at: <timestamp|null>

  low_quality_v3_6h:
    is_low: <bool>
    score: <float>
    threshold: <float|null>
    updated_at: <timestamp|null>
```

---

## 🖥 Systemd Services — Full List

- `yt-auto-discover`
- `yt-auto-track`
- `yt-lowq-3h`
- `yt-lowq-6h`
- `yt-kpis`
- `yt-viral-finalize`
- `yt-viral` (optional if running one line)
- `yt-viral-6h`
- `yt-viral-12h`
- `yt-viral-24h`
- `yt-dashboard` (optional if using tmux)

### Symlink systemd service

Run each service

```bash
sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-auto-discover.service \
    /etc/systemd/system/yt-auto-discover.service

sudo ln -sf /home/ytscan/yt-autoscanner/systemd/yt-viral-6h.service \
    /etc/systemd/system/yt-viral-6h.service

sudo ln -sf /home/ytscan/yt-autoscanner/systemd/yt-viral-12h.service \
    /etc/systemd/system/yt-viral-12h.service

sudo ln -sf /home/ytscan/yt-autoscanner/systemd/yt-viral-24h.service \
    /etc/systemd/system/yt-viral-24h.service

sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-auto-track.service \
    /etc/systemd/system/yt-auto-track.service

sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-lowq-3h.service \
    /etc/systemd/system/yt-lowq-3h.service

sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-lowq-6h.service \
    /etc/systemd/system/yt-lowq-6h.service

sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-kpis.service \
    /etc/systemd/system/yt-kpis.service

sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-viral-finalize.service \
    /etc/systemd/system/yt-viral-finalize.service

sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-ad-friendly.service \
    /etc/systemd/system/yt-ad-friendly.service
```

Or just one link

```bash
sudo ln -s /home/ytscan/yt-autoscanner/systemd/yt-*.service /etc/systemd/system/
```

Check the services

```bash
ls -l /etc/systemd/system/yt-*.service
```

### Enable

```bash
sudo systemctl enable yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-viral-6h yt-viral-12h yt-viral-24h yt-kpis yt-viral-finalize yt-ad-friendly yt-kw-stats
```

### Start

```bash
sudo systemctl start yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-viral-6h yt-viral-12h yt-viral-24h yt-kpis yt-viral-finalize yt-ad-friendly yt-kw-stats
```

### Checking status

```bash
systemctl status yt-auto-discover
systemctl status yt-auto-track
systemctl status yt-lowq-3h
systemctl status yt-lowq-6h
systemctl status yt-kpis
systemctl status yt-viral-6h
systemctl status yt-viral-12h
systemctl status yt-viral-24h
systemctl status yt-viral-finalize
systemctl status yt-ad-friendly
systemctl status yt-kw-stats
```

### Stop

```bash
sudo systemctl stop yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-viral-6h yt-viral-12h yt-viral-24h yt-kpis yt-viral-finalize yt-ad-friendly yt-kw-stats
```

### Restart

```bash
sudo systemctl restart yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-viral-6h yt-viral-12h yt-viral-24h yt-kpis yt-viral-finalize yt-ad-friendly yt-kw-stats
```

### Logs

```bash
journalctl -u yt-auto-discover -f
journalctl -u yt-auto-track -f
journalctl -u yt-lowq-3h -f
journalctl -u yt-lowq-6h -f
journalctl -u yt-kpis -f
journalctl -u yt-viral-6h -f
journalctl -u yt-viral-12h -f
journalctl -u yt-viral-24h -f
journalctl -u yt-viral-finalize -f
journalctl -u yt-ad-friendly -f
journalctl -u yt-kw-stats -f
```

### Disable

```bash
sudo systemctl disable yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-kpis yt-viral-6h yt-viral-12h yt-viral-24h yt-viral-finalize yt-ad-friendly yt-kw-stats
```

### Completely stop and remove

```bash
sudo systemctl stop yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-kpis yt-viral-6h yt-viral-12h yt-viral-24h yt-viral-finalize yt-ad-friendly  && \
sudo systemctl disable yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-kpis yt-viral-6h yt-viral-12h yt-viral-24h yt-viral-finalize yt-ad-friendly yt-kw-stats
```

---

## 🧩 Dashboard Runtime Modes  
### **Option 1 — Production Mode (`systemd`)**

```ini
[Unit]
Description=YT Autoscanner Streamlit Dashboard
After=network.target

[Service]
Type=simple
User=ytscan
WorkingDirectory=/home/ytscan/yt-autoscanner

ExecStart=/home/ytscan/yt-autoscanner/.venv/bin/streamlit run dashboard/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true

Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

---

### **Option 2 — Development Mode (tmux)**  

```bash
tmux new -s dashboard
source .venv/bin/activate
streamlit run dashboard/app.py --server.port=8501 --server.address=0.0.0.0
```

Detach:

```
Ctrl + B, then D
```

Reattach:

```bash
tmux attach -t dashboard
```

Kill:

```bash
tmux kill-session -t dashboard
```

---

## 🔐 Backups

Daily:

```bash
scripts/mongo_backup.sh
```

Full:

```bash
scripts/mongo_backup-full.sh
```

Saved in:

```
/home/ytscan/mongo_backups/
```

---

## 🧩 Full Data Flow

```
Discover → Track → ML Auto-Flag (3h/6h) → Viral Scoring (6h/12h/24h) → Finalize Viral (≥24h) → process_data → Dashboard/API
```

---

## ✨ Author

Developed by **Anh Quan Bui**  
Post-Graduate Certificate — Data Analytics & AI  
Saskatchewan Polytechnic, Canada
