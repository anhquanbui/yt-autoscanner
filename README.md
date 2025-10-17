# Researcher Profile — Saskatchewan Polytechnic (Post-Graduate Certificate in Data Analytics & AI)

**Name:** Anh Quan Bui  
**Email:** bui8334@saskpolytech.ca  
**Affiliation:** Saskatchewan Polytechnic – School of ICT  
**Program:** Post-Graduate Certificate in Data Analytics & AI  
**Research Project:** *YouTube Video Virality Prediction Using Early Engagement Signals*  
**Summary:** This project investigates how early metrics (views, likes, comments) from YouTube Data API v3 can be used to predict the long-term popularity of videos. It combines data ingestion (via FastAPI and MongoDB) with machine learning modeling (XGBoost) to identify viral potential within the first 24 hours after upload.  
**Purpose:** To contribute an academic framework and prototype system supporting data-driven content marketing and social media analysis.

---

# yt-autoscanner — Local Dev (API + MongoDB + YouTube Worker + Scheduler)

A minimal starter to ingest **YouTube** videos into **MongoDB** and expose them via a **FastAPI** API.  
This README covers local development, environment config, the discover worker (v4.1), the tracker, backfill utilities, and the unified PowerShell runner.

> **Quick links**
> - API (local): `http://127.0.0.1:8000/docs`
> - Mongo (local): `mongodb://localhost:27017/ytscan` (`videos`, `channels` collections)
> - Logs (local): `./logs/scanner-YYYYMMDD.log`

---

## What's new (2025‑10‑17)

### 🆕 `worker\process_data.py` — **v1.0**
- New CLI script for **processing local JSON data** and **pushing results to MongoDB** automatically.
- Loads `.env` for `MONGO_URI` and uses the `videos` collection by default.
- Supports optional `--query` filter or JSON file input.
- Automatically creates missing collections and indexes if not found.
- Default behavior: pushes data directly to MongoDB (`insert_many`).

### Example usage
```bash
# Basic usage (auto push)
python process_data.py

# Custom Mongo URI or collection
python process_data.py --mongo-uri "mongodb://localhost:27017" --db ytscan --collection videos

# Optional query mode
python process_data.py --query "{'region': 'US'}"
```

👉 See full guide: [process_data_README.md](process_data_readme.md)

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
│  └─ process_data.py   ← NEW
├─ run_both_local.ps1
├─ .env
├─ logs/
│  └─ scanner-YYYY-MM-DD.log
└─ README.md
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
# pip install -r tools/requirements.txt # (if it is available)
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
.
un_both_local.ps1
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
Get-Content .\logs	ranscript-$(Get-Date -Format yyyyMMdd).log -Tail 100 -Wait
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

📅 **Last updated:** 2025‑10‑17