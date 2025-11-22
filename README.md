
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
│   └── low_quality/
│       ├── low_quality_model_3h.joblib
│       └── low_quality_model_6h.joblib
│
├── scripts/
│   ├── auto_discover.sh
│   ├── auto_kpis.sh
│   ├── auto_lowq_3h.sh
│   ├── auto_lowq_6h.sh
│   ├── auto_quality.sh
│   ├── auto_track.sh
│   ├── mongo_backup.sh
│   └── mongo_backup-full.sh
│
├── systemd/
│   ├── yt-auto-discover.service
│   ├── yt-auto-track.service
│   ├── yt-dashboard.service
│   ├── yt-kpis.service
│   ├── yt-lowq-3h.service
│   └── yt-lowq-6h.service
│
├── tools/
│   ├── make_indexes.py
│   ├── backfill_channels.py
│   ├── backfill_missing_fields.py
│   └── (other one-off scripts)
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
Runs ML models at 3h and 6h:

```text
ml_flags.low_quality_v1_3h
ml_flags.low_quality_v3_6h
```

Stops low-quality videos automatically.

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
YT_API_KEY=xxxx
MONGO_URI=mongodb://localhost:27017/ytscan
YT_RANDOM_PICK=1
YT_RANDOM_REGION_POOL=US,GB,CA,AU,IN,JP,VN,KR
LOW_QUALITY_MODEL_3H=models/low_quality/low_quality_model_3h.joblib
LOW_QUALITY_MODEL_6H=models/low_quality/low_quality_model_6h.joblib
LOG_LEVEL=INFO
```

---

## 📚 MongoDB Collections

| Collection | Purpose |
|-----------|---------|
| videos | Main ingestion + tracking |
| processed_videos | Cleaned dataset |
| channels | Channel metadata |
| dashboard_kpis_overview | Cached KPIs |
| worker_status | Worker heartbeat |
| worker_runs | Optional history |

Build indexes:

```bash
python tools/make_indexes.py
```

---

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
ml_flags.low_quality_v1_3h
ml_flags.low_quality_v3_6h
ml_flags.viral_v1
```

---

## 🖥 Systemd Services — Full List

- `yt-auto-discover`
- `yt-auto-track`
- `yt-lowq-3h`
- `yt-lowq-6h`
- `yt-kpis`
- `yt-dashboard` (optional if using tmux)

### Start

```bash
sudo systemctl start yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-kpis yt-dashboard
```

### Stop

```bash
sudo systemctl stop yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-kpis yt-dashboard
```

### Restart

```bash
sudo systemctl restart yt-auto-discover yt-auto-track yt-lowq-3h yt-lowq-6h yt-kpis yt-dashboard
```

### Logs

```bash
journalctl -u yt-auto-discover -f
journalctl -u yt-auto-track -f
journalctl -u yt-lowq-3h -f
journalctl -u yt-lowq-6h -f
journalctl -u yt-kpis -f
journalctl -u yt-dashboard -f
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
Discover → Track → ML Auto-Flag → process_data → Dashboard/API
```

---

## ✨ Author

Developed by **Anh Quan Bui**  
Post-Graduate Certificate — Data Analytics & AI  
Saskatchewan Polytechnic, Canada
