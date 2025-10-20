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

## What's new (2025-10-17)

### 🆕 `worker\process_data.py` — **v1.0**
- New CLI script for **processing local JSON data** and **pushing results to MongoDB** automatically.
- Loads `.env` for `MONGO_URI` and uses the `videos` collection by default.
- Supports optional `--query` filter or JSON file input.
- Automatically creates missing collections and indexes if not found.
- Default behavior: pushes data directly to MongoDB (`insert_many`).

#### Example usage
```bash
# Basic usage (auto push)
python process_data.py

# Custom Mongo URI or collection
python process_data.py --mongo-uri "mongodb://localhost:27017" --db ytscan --collection videos

# Optional query mode
python process_data.py --query "{'region': 'US'}"
```

👉 See full guide: [process_data_readme.md](docs/process_data_readme.md)

---

## What's new (2025-10-20)

### ⚙️ `worker\discover_once.py` — **v4.3**
- Added **automatic filtering** to exclude `live` and `upcoming` videos during discovery.
- Maintains duration-based sampling (`short`, `medium`, `long`, `any`, `mix`) for flexible coverage.
- Keeps full snippet enrichment (categoryId, durationISO, durationSec, lengthBucket).
- Logs filtered counts for better visibility during scans.
- Backward compatible with `.env` configuration from v4.2.

### ⚙️ `worker\track_once.py` — **v3.1**
- Introduced optimized **backfill handling** for missing duration or length buckets.
- Automatically skips live/upcoming videos and ensures consistent milestone polling.
- Improved error handling and quota recovery for `quotaExceeded` events.
- Refactored backfill to trigger only when duration fields are missing.

### 🧰 `tools/backfill_missing_fields.py` — **v1.0** (new)
- New standalone script designed to backfill videos that are already `complete` or partially enriched.
- Handles missing fields such as:
  - `snippet.durationISO`, `snippet.durationSec`, `snippet.lengthBucket`
  - `snippet.channelHandle` (via channel lookup)
- Supports CLI arguments for batch size, dry-run, and limit filtering.
- Keeps tracking and discovery independent from data repair tasks.

### ⚡ `run_both_local.ps1` — **v5.1**
- Enhanced compatibility with PowerShell 7+ (UTF-8 logs + color-safe output).
- Improved log timestamping and error capture for both discover and track workers.
- Automatically pauses between runs when API quota is exhausted.
- Adds flag to optionally run `tools/backfill_missing_fields.py` once a day.

### 📁 Documentation updates
- README updated with new “What’s new (2025-10-20)” section.
- Added internal reference in Docs to `backfill_missing_fields.py` under `tools/`.
- Updated project progress table and `Last updated` timestamp.

---


### Worker (`discover_once.py`) — **v4.2**
- Refactored for **lightweight near-now scan** (no lookback > 24h).
- Keeps **categoryId** enrichment (1 quota per 50 videos).
- Simplified: skips `topicId`, `channelHandle`, random by weighted query & region pool.
- Random mode: dynamically selects region & query (hot topics weighted).

### Tracker (`track_once.py`) — **v3.0**
- Monitors video statistics (views, likes, comments) until **24h**.
- Uses **fine-grained milestones** for ML-friendly time series (every 5m→60m).
- Marks video complete after 24h or unavailable.

### Backfill (`tools/backfill_channels_v2.py`) — **v2**
- Adds channel metadata & statistics (`subscriberCount`, `videoCount`, `viewCount`).
- Auto-detects missing or stale documents (older than X hours).
- Supports dry-run mode for safe testing.

### Unified Runner (`run_both_local.ps1`) — **v5**
- Runs both **discover** and **track** loops on schedule.
- Reads `.env` automatically (no hardcoded keys).
- Logs in real time to `./logs/transcript-YYYYMMDD.log`.
- Auto-stops on quota exhaustion (exit code 88).

---

## Project structure

```
yt-autoscanner/
├─ docs/
│  ├─ mongo_collections_overview.md
│  ├─ pipeline_overview.md
│  ├─ process_data_readme.md
│  └─ processed_videos_explanation.md
├─ api/
│  ├─ main.py
│  └─ requirements.txt
├─ worker/
│  ├─ discover_once.py
│  ├─ track_once.py
│  ├─ scheduler.py
│  └─ requirements.txt
├─ tools/
│  ├─ make_indexes.py
│  ├─ backfill_channels_v2.py
│  └─ process_data.py
├─ logs/
│  └─ scanner-YYYY-MM-DD.log
├─ .env
├─ .gitignore
├─ auto-track.ps1
├─ dashboard_summary.json
├─ processed_videos.json
├─ README.md
├─ requirements.txt
├─ run_both_local.ps1
├─ seed.py
├─ ytscan.channels.json
└─ ytscan.videos.json
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

## Local setup

### 1️⃣ Python & venv (Windows PowerShell)
```powershell
python -m venv venv
# If blocked once:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.venv\Scripts\Activate.ps1
```

### 2️⃣ Install dependencies
```powershell
pip install -r requirements.txt
pip install -r api/requirements.txt
pip install -r worker/requirements.txt
# pip install -r tools/requirements.txt  # (if available)
```

### 3️⃣ MongoDB
```powershell
docker run -d --name mongo -p 27017:27017 mongo:7
python tools/make_indexes.py
```

### 4️⃣ Run API
```powershell
cd api
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Swagger UI → http://127.0.0.1:8000/docs
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