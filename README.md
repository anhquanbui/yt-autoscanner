# 🎯 YouTube AutoScanner — VPS Deployment Edition (2025)

This repository powers a full 24/7 automation pipeline for discovering, tracking, and evaluating YouTube videos in their first 24 hours.  
It is designed for **VPS deployment**, using `systemd`, `cron`, MongoDB, and Python workers.

---

## 📦 Project Structure (2025 Updated)

```
yt-autoscanner/
│
├── api/
│   ├── main.py
│   └── config/
│       └── path_utils.py
│
├── config/
│   └── path_utils.py
│
├── logs/
│
├── models/
│   └── low_quality/
│       ├── low_quality_model_3h.joblib
│       └── low_quality_model_6h.joblib
│
├── tools/
│   ├── auto_discover.sh
│   ├── auto_quality.sh
│   ├── auto_track.sh
│   ├── make_indexes.py
│   ├── backfill_channels.py
│   ├── backfill_missing_fields.py
│   ├── mongo_backup.sh
│   └── mongo_backup-full.sh
│
├── worker/
│   ├── discover_once.py
│   ├── low_quality_autoflag.py
│   ├── process_data.py
│   └── track_once.py
│
├── README.md
├── requirements-dev.txt
└── .gitignore
```

---

## 🔧 Key Components

### Discover Worker — `discover_once.py`
Discovers new videos, inserts into MongoDB, schedules first tracking time.

### Track Worker — `track_once.py`
Tracks video performance from 0 → 24h using milestone scheduling.

### Low-Quality ML — `low_quality_autoflag.py`
Runs ML models at 3h & 6h to assign:
```
ml_flags.low_quality_v1_3h.is_low
ml_flags.low_quality_v3_6h.is_low
```
Updates:
```
status = stopped
stop_reason = "low_quality"
```

### process_data.py
Cleans → aggregates → exports Parquet for dashboards + ML.

### tools/
Contains:
- auto runners for systemd
- index builder
- backup scripts
- backfill utilities

---

## 🔑 Environment Variables

Stored at:

```
/home/ytscan/.env
```

Example:

```
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
| videos | full ingestion + tracking |
| processed_videos | cleaned dataset |
| channels | channel metadata |
| dashboard_summary | cached dashboard data |

Run indexes:
```
python tools/make_indexes.py
```

---

## 🧠 Video Schema (Latest 2025)

### Core Fields
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
status: tracking | complete | stopped
stop_reason
next_poll_after
snapshots: [...]
```

### ML Flags
```
ml_flags: {
  low_quality_v1_3h: { is_low, score },
  low_quality_v3_6h: { is_low, score }
}
```

---

## 🔄 Processing Intervals

| Component | Frequency |
|----------|-----------|
| Discover | 10–12s |
| Track | 5–6s |
| ML | 10–15s |
| Process Data | every 6h |

---

## 🖥 Service Controls (Start / Stop / Restart)

### ▶ Start all services
```
sudo systemctl start yt-auto-discover yt-auto-track yt-auto-quality yt-api
```

### ⏹ Stop all services
```
sudo systemctl stop yt-auto-discover yt-auto-track yt-auto-quality yt-api
```

### 🔄 Restart all services
```
sudo systemctl restart yt-auto-discover yt-auto-track yt-auto-quality yt-api
```

### Check status
```
systemctl status yt-auto-discover
systemctl status yt-auto-track
systemctl status yt-auto-quality
systemctl status yt-api
```

---

## 📺 API (FastAPI)

```
sudo systemctl restart yt-api
```

Endpoints:
```
/videos/recent
/videos/stats
/dashboard/summary
/channels/info
```

---

## 📉 Monitoring

```
journalctl -u yt-auto-discover -f
journalctl -u yt-auto-track -f
journalctl -u yt-auto-quality -f
journalctl -u yt-api -f
```

System:
```
htop
df -h
free -m
```

---

## 🔐 Backups

Daily backup:
```
tools/mongo_backup.sh
```

Full backup:
```
tools/mongo_backup-full.sh
```

Stored in:
```
/home/ytscan/mongo_backups/
```

---

## 🧩 Full Data Flow

```
Discover
   ↓
Insert videos
   ↓
Track (0→24h)
   ↓
ML Auto-Flag (3h / 6h)
   ↓
process_data → Parquet
   ↓
Dashboard / ML Training / API
```

---

## ✨ Author

Developed by **Anh Quan Bui**  
Post-Graduate Certificate — Data Analytics & AI  
Saskatchewan Polytechnic, Canada
