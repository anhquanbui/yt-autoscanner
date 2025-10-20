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
This README covers local development, environment config, the discover worker (v4.1), the tracker, backfill utilities, and the unified PowerShell runner.

> **Quick links**
> - API (local): `http://127.0.0.1:8000/docs`
> - Mongo (local): `mongodb://localhost:27017/ytscan`
> - Logs (local): `./logs/scanner-YYYYMMDD.log`

---

## 📘 Docs Overview

| File | Description |
|------|--------------|
| [`mongo_collections_overview.md`](docs/mongo_collections_overview.md) | Explains all MongoDB collections and their key fields |
| [`pipeline_overview.md`](docs/pipeline_overview.md) | Describes end-to-end ingestion → tracking → processing pipeline |
| [`process_data_readme.md`](docs/process_data_readme.md) | Detailed documentation for `process_data.py` usage and logic |
| [`processed_videos_explanation.md`](docs/processed_videos_explanation.md) | Explains structure and meaning of `processed_videos.json` |

---

## What's new (Oct 20 2025)
- **Discover Worker v4.3** — Added automatic filtering to skip live and upcoming videos.
- **Track Worker v3.1** — Enhanced duration backfill logic for videos missing duration data.
- **Backfill Tool v1.0** — Introduced `tools/backfill_missing_fields.py` to fill missing duration/handles independently.
- **.gitignore** — Now excludes `.bak` backup files.

## What's new (Oct 17 2025)
- **Process Data Script (v1.0)** — Automates inserting JSON into MongoDB.
- **Discover Worker (v4.2)** — Adds duration enrichment and random region/query weighting.
- **Track Worker (v3.0)** — Tracks video metrics at multiple milestones (up to 24h).
- **Unified Runner (v5)** — Real-time logs and quota protection.

👉 **View full changelog → [CHANGELOG.md](CHANGELOG.md)**

---

## Project structure

```
YT-AUTOSCANNER/
│
├─ api/                           # FastAPI backend
│   ├─ main.py                    # API entry point (Uvicorn server)
│   └─ requirements.txt           # API dependencies
│
├─ docs/                          # Internal documentation & references
│   ├─ Autorun_Scripts_Guide.md
│   ├─ mongo_collections_overview.md
│   ├─ pipeline_overview.md
│   ├─ process_data_readme.md
│   └─ processed_videos_explanation.md
│
├─ logs/                          # Log output from workers / API
│
├─ tools/                         # Helper utilities
│   ├─ backfill_channels_v2.py
│   ├─ backfill_missing_fields.py
│   └─ make_indexes.py
│
├─ worker/                        # Data ingestion and tracking logic
│   ├─ discover_once.py           # Discover new YouTube videos
│   ├─ track_once.py              # Track video stats over time (1h→24h)
│   ├─ scheduler.py               # Optional scheduler for periodic tasks
│   ├─ process_data.py            # Clean & aggregate raw data
│   └─ requirements.txt           # Worker dependencies
│
├─ venv/                          # Local Python virtual environment (ignored by Git)
│
├─ .env                           # Environment variables (Mongo URI, API key, etc.)
├─ .gitignore                     # Ignore venv, logs, cache files
├─ requirements-dev.txt            # Full dev setup (worker + API + dashboard)
├─ README.md                      # Documentation and setup instructions
├─ CHANGELOG.md                   # Version changes and release notes
├─ run_both_local.ps1             # Run discover + track together (PowerShell)
├─ run_track_one_loop_30s.ps1     # Loop runner for track_once.py (demo/test)
├─ auto-track.ps1                 # Auto-tracking shortcut script
└─ seed.py                        # Seed data helper or test initialization

```

---

## API

### Endpoints
- `GET /health`
- `GET /videos`
- `GET /video/{id}`
- `GET /tracking`
- `GET /complete`
- `GET /videos/count`
- `GET /stats`
- *(future)* `/channels` for enriched channel info

---

## 🧬 Local Setup

### 1️⃣ Python & Virtual Environment (Windows PowerShell)

```powershell
# Create a new virtual environment
python -m venv venv

# If activation is blocked once:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# Activate the environment
.\venv\Scripts\Activate.ps1
```

---

### 2️⃣ Install Dependencies

You have two options depending on your workflow:

#### 🧩 Option A — Install by module

```powershell
# API (FastAPI backend)
pip install -r api/requirements.txt

# Worker (discover_once.py, track_once.py)
pip install -r worker/requirements.txt

# Dashboard (Streamlit UI)
pip install -r dashboard/requirements.txt
```

#### 🚀 Option B — Full development setup (everything)

```powershell
pip install -r requirements-dev.txt
```

> 💡 **Tip:**  
> Use **Option B** for local development when you plan to run all components (API + worker + dashboard).  
> Use **Option A** for deploying or testing individual modules.

---

### 3️⃣ MongoDB Setup

```powershell
# Run MongoDB in Docker
docker run -d --name mongo -p 27017:27017 mongo:7

# (Optional) create indexes if you have a helper script
python tools/make_indexes.py
```

---

### 4️⃣ Run the FastAPI Backend

```powershell
cd api
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Swagger UI → http://127.0.0.1:8000/docs
```

---

### 5️⃣ Run the Streamlit Dashboard (for future development)

```powershell
cd dashboard # dashboard PATH
streamlit run [name_of_the_file].py
# Default URL: http://localhost:8501
```

---

### 6️⃣ Run Worker Scripts (Data Ingestion / Tracking)

```powershell
# Discover new YouTube videos
python worker/discover_once.py

# Track video metrics over time (1h → 24h)
python worker/track_once.py

# Optional: run both in a loop (PowerShell helper script)
.\run_both_local.ps1
```

---

### ⚙️ Environment Variables (.env)

Ensure a `.env` file exists in the project root:

```env
# === MongoDB ===
MONGO_URI=mongodb://localhost:27017
DB_NAME=yt_autoscanner

# === YouTube API ===
YT_API_KEY=your_youtube_api_key_here
```

---

## Environment (`.env`)

```
# --- Required ---
YT_API_KEY=YOUR_YOUTUBE_API_KEY
MONGO_URI=mongodb://localhost:27017/ytscan

# --- Common ---
YT_REGION=US
YT_SINCE_MODE=minutes
YT_SINCE_MINUTES=20

# --- Random mode ---
YT_RANDOM_MODE=1
YT_RANDOM_REGION_POOL=US,GB,JP,VN,CA,SG,PH,IN,AU
YT_RANDOM_QUERY_POOL=live:5,news:3,gaming:4,highlights:4,music:3,trailer:3,shorts:3,breaking:2,review:2,concert:2,update:2

# --- Quota safety ---
YT_MAX_PAGES=2
DISCOVER_INTERVAL_SECONDS=1800   # 30min
TRACK_INTERVAL_SECONDS=30        # 30s
```

---

## Workers

### 🔹 Discover Once
```powershell
python worker/discover_once.py
```
Discovers recent videos (within last 20 minutes).  
Saves `categoryId`, region, query, timestamp.  
Quota usage: ~101 units per 50 videos.

---

### 🔹 Track Once
```powershell
python worker/track_once.py
```
Tracks stats per milestone → snapshots every few minutes up to 24h.  
Quota: ~1 unit per 50 videos.

---

### 🔹 Process Data
```powershell
python tools/process_data.py
```
Processes local JSON data and automatically inserts it into MongoDB.  
- Loads `.env` for default `MONGO_URI`.  
- Supports manual overrides via CLI args.  
- Pushes directly to MongoDB unless `--dry-run` specified.  

Example:
```powershell
python tools/process_data.py --mongo-uri "mongodb://localhost:27017" --db ytscan --collection videos
```

---

### 🔹 Backfill Channels
```powershell
python tools/backfill_channels_v2.py --stale-hours 24
```
Refreshes missing or outdated channel stats.  
Dry-run mode:
```powershell
python tools/backfill_channels_v2.py --dry-run
```

---

## Unified Runner (PowerShell)

### Run both discover + tracker
```powershell
# If blocked once:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run_both_local.ps1
```
- Loads `.env` automatically  
- Writes logs to `/logs/`  
- Auto-sleeps between runs  
- Stops safely on quota exhaustion

---

## Logging
- Logs are written to `logs/` with rotating daily filenames.  
- Each run includes timestamped activity from both workers.

**Tail logs live:**
```powershell
Get-Content .\logs\transcript-$(Get-Date -Format yyyyMMdd).log -Tail 100 -Wait
```

---

## Monitoring (MongoDB Compass)
Connect to:
```
mongodb://localhost:27017/ytscan
```
Collections:
- `videos` → discovered & tracked videos
- `channels` → enriched metadata & stats

Useful queries:
- `{ "tracking.status": "tracking" }`
- `{ "tracking.status": "complete" }`
- Sort by `snippet.publishedAt` or `tracking.discovered_at`

---

## Next steps
- Add ML model training for early-viral prediction using `stats_snapshots`.
- Implement FastAPI endpoints for `/channels` data.
- Add Docker Compose for full stack (API + MongoDB + Workers).

---

## 📊 System Status Overview (as of 2025-10-17) (PROJECT TRACKING)

| Component | Description | Status | Completion |
|------------|--------------|:------:|:-----------:|
| **Core API** | FastAPI endpoints (`/videos`, `/health`, `/tracking`, etc.) | ✅ Stable | **100%** |
| **MongoDB Integration** | Collections (`videos`, `channels`) with proper indexes | ✅ Complete | **100%** |
| **Worker — Discover** | Scans latest videos by region/query | ✅ Functional | **100%** |
| **Worker — Track** | Monitors statistics every milestone (up to 24h) | ✅ Stable | **100%** |
| **Worker — Process Data** | Processes JSON → inserts into MongoDB automatically | ✅ Complete | **100%** |
| **Worker — Backfill Channels** | Updates channel metadata and stats | ✅ Complete | **100%** |
| **Temporal Sampling Plan** | Refined to **64–65 timestamps / 24h** (dynamic frequency) | ✅ Updated | **100%** |
| **Logging & Scheduler** | PowerShell unified runner (`run_both_local.ps1`) and log rotation | ✅ Verified | **100%** |
| **Documentation (Docs + README)** | Unified formatting, consistent structure across all `.md` files | ✅ Synced | **100%** |
| **Local Testing** | MongoDB + API + Worker integration tests | ⚙️ Partial | **75%** |
| **Machine Learning Stage** | Feature extraction + XGBoost model training | 🔜 Pending | **20%** |
| **Visualization / Dashboard** | Optional analytics dashboard (Power BI / Streamlit) | 🧩 Planned | **15%** |

---

### 🧠 Summary
- **Core system (Ingestion + Tracking + Processing):** ✅ **Complete (~85%)**  
- **Full project (including ML & Dashboard):** 🚀 **~65% overall progress**

> Next steps:
> 1. Implement ML model for early virality prediction.  
> 2. Add `/predict` and `/channels` endpoints in FastAPI.  
> 3. Build analytics dashboard for visualization and reporting.

---


📅 **Last updated:** 2025-10-20