# Researcher Profile — Saskatchewan Polytechnic (Post-Graduate Certificate in Data Analytics & AI)

**Name:** Anh Quan Bui  
**Email:** bui8334@saskpolytech.ca  
**Affiliation:** Saskatchewan Polytechnic – School of ICT  
**Program:** Post-Graduate Certificate in Data Analytics & AI  
**Research Project:** *YouTube Video Virality Prediction Using Early Engagement Signals*  
**Summary:** This project investigates how early metrics (views, likes, comments) from YouTube Data API v3 can be used to predict the long-term popularity of videos. It combines data ingestion (via FastAPI and MongoDB) with machine learning modeling (XGBoost) to identify viral potential within the first 24 hours after upload.  
**Purpose:** To contribute an academic framework and prototype system supporting data-driven content marketing and social media analysis.

---

## 👥 Team Members
- **Anh Quan Bui** — [GitHub](https://github.com/anhquanbui) *(Lead Researcher / System Architect)*  
- **Eneyi Simeni** — [GitHub](https://github.com/Eneyi1403) *(Data Engineer / Pipeline Development)*  
- **Nguyen Ha Dung** — [GitHub](https://github.com/HaDung-Nguyen-000526332) *(Machine Learning & Model Evaluation)*  

---

# yt-autoscanner — Local Dev (API + MongoDB + YouTube Worker + Scheduler)

A minimal starter to ingest **YouTube** videos into **MongoDB** and expose them via a **FastAPI** API.  
This README covers local development, environment config, the discover worker (v4.3), the tracker, backfill utilities, and the unified PowerShell runner.

> **Quick links**
> - API (local): `http://127.0.0.1:8000/docs`
> - Mongo (local): `mongodb://localhost:27017/ytscan`
> - Logs (local): `./logs/scanner-YYYYMMDD.log`

---

## 📁 Documentation Files

### 🛰️ Ingestion — Discover & Track
| File | Description |
|------|-------------|
| 📌 [discover_once.md](docs/discover_once.md) | Discovers newly published videos via region + keyword pools |
| 📈 [track_once.md](docs/track_once.md) | Collects early engagement time‑series snapshots |

### 🔄 Data Lifecycle — Maintain a Clean & Scalable Dataset
| File | Description |
|------|-------------|
| 🧊 [archive_completed_videos.md](docs/archive_completed_videos.md) | Moves completed videos → cold storage (monthly partitions) |
| 🗑️ [prune_unavailable_once.md](docs/prune_unavailable_once.md) | Removes private/deleted/no‑data videos to reduce bloat |

### 🗄️ Database Schema & Performance
| File | Description |
|------|-------------|
| 🧩 [ytscan_collections_overview.md](docs/ytscan_collections_overview.md) | Database schema & relations (hot + cold storage) |
| 🚀 [make_indexes_v3.md](docs/make_indexes_v3.md) | Performance‑optimized MongoDB indexes |

### ⚙️ Operations & Automation
| File | Description |
|------|-------------|
| 🕒 [Autorun_Scripts_Guide.md](docs/Autorun_Scripts_Guide.md) | Automated scheduling for ingestion tasks |
| 🔧 [mongodb_setup_for_beginners.md](docs/mongodb_setup_for_beginners.md) | Setup local MongoDB / Compass |

### 🧪 Data Processing & Analytics Pipeline
| File | Description |
|------|-------------|
| 🧼 [process_data_v6_usage.md](docs/process_data_v6_usage.md) | Aggregation + feature processing workflow [explanation](docs/explanation_processed_videos.md) |
| 🗺️ [pipeline_overview.md](docs/pipeline_overview.md) | Full pipeline flow: Discover → Track → Archive/Prune → ML |

---

## What's new (Oct 26 2025)
- **discover_once.py** — Removed channelTitle + lightweight insertion
- **track_once.py** — Clarified stop lifecycle, stronger polling logic docs
- **archive_completed_videos.py** — Monthly partitioned cold storage
- **prune_unavailable_once.py** — Hard delete unavailable/no-publishedAt videos
- **Docs Update** — English documentation for 4 core tools
- **backfill_channels.py** — Update cho create channels collection

## What's new (Oct 24 2025)
- **make_indexes.py** — Version 3 with sub-index

## What's new (Oct 22 2025)
- **make_indexes.py** — Version 2: Enhanced MongoDB index management

## Project structure
```
YT-AUTOSCANNER/
├─ api/                         # FastAPI backend
│   └─ main.py
│
├─ docs/                        # Internal documentation (English ✅)
│   ├─ discover_once.md
│   ├─ track_once.md
│   ├─ archive_completed_videos.md
│   ├─ prune_unavailable_once.md
│   ├─ ytscan_collections_overview.md
│   ├─ pipeline_overview.md
│   ├─ process_data_v6_usage.md
│   ├─ make_indexes_v3.md
│   ├─ Autorun_Scripts_Guide.md
│   └─ mongodb_setup_for_beginners.md
│
├─ tools/                       # Data lifecycle & maintenance tools
│   ├─ archive_completed_videos.py   # moves complete videos → cold
│   ├─ prune_unavailable_once.py     # removes broken/unavailable docs
│   ├─ backfill_channels.py
│   ├─ backfill_missing_fields.py
│   ├─ make_indexes.py
│   └─ index_maintenance.log
│
├─ worker/                      # Video ingestion + tracking core
│   ├─ discover_once.py              # Discover fresh videos
│   ├─ track_once.py                 # Track early engagement stats
│   ├─ scheduler.py                  # (optional) automated jobs
│   ├─ process_data.py
│   └─ requirements.txt
│
├─ logs/                        # Worker/API logs (rotating daily)
├─ .env                         # Mongo + YouTube API key
├─ CHANGELOG.md                 # Version history
├─ README.md                    # You are here 👋
├─ dashboard_summary.json       # Statistics for dashboard
├─ run_both_local.ps1           # Unified worker runner
└─ seed.py                      # Sample initializer or testing
```

---

## API Endpoints
- `GET /health`
- `GET /videos`
- `GET /video/{id}`
- `GET /tracking`
- `GET /complete`
- `GET /videos/count`
- `GET /stats`

*(future)* `/channels` — channel-level insights

---

## 🧬 Local Setup Guide
### 1️⃣ Virtual Environment
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2️⃣ Install Dependencies
```powershell
pip install -r requirements-dev.txt
```
> Use this for full-stack local development

---

### 3️⃣ MongoDB Setup
Via Docker:
```powershell
docker run -d --name mongo -p 27017:27017 mongo:7
python tools/make_indexes.py
```
Via Compass — guide → `mongodb_setup_for_beginners.md`

---

### 4️⃣ Run API
```powershell
cd api
uvicorn main:app --reload
```

### 5️⃣ Run Workers
```powershell
python worker/discover_once.py
python worker/track_once.py
```

Unified runner:
```powershell
.\run_both_local.ps1
```

---

## Collections
- `videos` — Active tracking pool
- `channels` — Channel enrichment metadata

Useful queries:
```js
{ "tracking.status": "tracking" }
{ "tracking.status": "complete" }
```

---

## 📊 Current System Status (as of 2025-10-26)

| Component | Status | Progress |
|----------|:------:|:-------:|
| API | ✅ Stable | 100% |
| MongoDB Indexing | ✅ Tuned | 100% |
| Discover Worker | ✅ Running | 100% |
| Track Worker | ✅ Running | 100% |
| Data Lifecycle (Archive + Prune) | ✅ Complete | **100%** |
| Backfill Tools | ✅ Complete | 100% |
| Documentation | ✅ Updated | 90% |
| ML Stage | 🚧 In progress | 25% |
| Dashboard | 📌 Planned | 20% |
| Deployment CI (Docker Compose + VPS) | 🚧 Planned | 10% |

---

### ✅ TL;DR Progress Summary
➡️ **Core ingestion + lifecycle = 93% complete**  
➡️ **Entire research project = 71% completion**  
✅ Data pipeline is **ready for ML training stage** 🚀

---

## Compliance Notice
This repository follows **YouTube Researcher Program policies**.  
No YouTube Data API content is included in this repo.

📅 **Last Updated:** **Oct 26 2025**