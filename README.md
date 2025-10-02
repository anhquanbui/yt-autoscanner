# yt-autoscanner — Local Dev (API + MongoDB + YouTube Worker + Scheduler)

A minimal starter to ingest YouTube videos into **MongoDB** and expose them via a **FastAPI** API.  
This version includes: extended API filters/sorting, a **v4 discover worker** (random mode + since‑mode by local time, UTF‑8 safe, `.env` support), and a **PowerShell loop runner** with optional Windows Task Scheduler autorun.

---

## What's new (since your last README)
- **API**
  - New: `GET /videos/count` (count with same filters as `/videos`)
  - New: `GET /stats` (totals by status + latest discovered timestamp)
  - `/videos` now supports: `q`, `channel_id`, `since`, `before`, `sort=published|discovered`, `order=asc|desc`
- **Worker `discover_once.py` (v4)**
  - **Random Mode** with region/keyword pools and random time slice
  - **SINCE_MODE**: `lookback` (default) / `now` / `minutes` / `local_midnight` (uses `YT_LOCAL_TZ`)
  - Auto‑resolve `@handle` → `channelId`
  - Category filter + graceful fallback to `mostPopular` when using a category
  - UTF‑8 safe logging (no emoji crashes), `.env` support (`python-dotenv`)
- **Runner**
  - `run_discover_loop.ps1`: activate venv, set env, ensure UTF‑8, create `logs/`, and call `worker/scheduler.py` every N seconds
  - Optional autorun via **Scheduled Task** (install/uninstall scripts)
  - Note: **use only one runner** (`run_discover_loop.ps1` *or* a simple `run_scheduler.ps1`) to avoid duplicate loops

> **Important**
> - Keep your **API key out of git** — prefer `.env`.  
> - Run **only one** discover loop at a time (to avoid wasting API quota).  
> - If Windows blocks scripts, use `Unblock-File` or `-ExecutionPolicy Bypass` once.

---

## Project Structure
```
yt-autoscanner/
├─ api/
│  ├─ main.py                   # FastAPI app
│  └─ requirements.txt
├─ worker/
│  ├─ discover_once.py          # v4: random mode, since-mode, @handle resolve, category filter + fallback
│  └─ scheduler.py              # simple loop; called by run_discover_loop.ps1
├─ run_discover_loop.ps1        # main runner (venv + env + UTF‑8 + logs + scheduler)
├─ (optional) run_scheduler.ps1 # minimal runner; don't use together with run_discover_loop.ps1
├─ venv/
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
- `GET /videos/count`  ← **new**
- `GET /stats`         ← **new**

### `/videos` query params
- `status`: `tracking` | `complete`
- `q`: case‑insensitive substring on `snippet.title` / `snippet.channelTitle`
- `channel_id`: filter by `snippet.channelId`
- `since`: ISO 8601 or relative (e.g. `PT2H`, `30m`) → `publishedAt >= since`
- `before`: ISO 8601 → `publishedAt < before`
- `sort`: `published` (default) | `discovered`
- `order`: `desc` (default) | `asc`
- `limit`: default 50 (1–200)

**Examples**
```
/videos?sort=discovered&order=desc&limit=20
/videos?status=tracking&limit=100
/videos?q=gaming&limit=50
/videos?channel_id=UCxxxxxxxx&limit=50
/videos?since=PT2H&limit=100
```
**Counts & Stats**
```
/videos/count
/videos/count?status=tracking&q=gaming
/stats
```

---

## Setup

### 1) Virtual environment (PowerShell)
```powershell
python -m venv venv
# If blocked: Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\venv\Scripts\Activate.ps1
```

### 2) Install dependencies
```powershell
pip install -r api/requirements.txt
# (add worker requirements here if you split them)
```

### 3) MongoDB
```powershell
# Local MongoDB service or Docker (mongo:7)
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
```

### 4) Run the API
```powershell
cd api
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Swagger: http://127.0.0.1:8000/docs
```

---

## Environment (.env recommended)
Create `.env` in project root (and add `.env` to `.gitignore`):
```
YT_API_KEY=YOUR_YOUTUBE_API_KEY
MONGO_URI=mongodb://localhost:27017/ytscan
YT_REGION=US
# optional:
# YT_LOOKBACK_MINUTES=360
# YT_FILTER_CATEGORY_ID=20
# --- random mode ---
# YT_RANDOM_MODE=1
# YT_RANDOM_LOOKBACK_MINUTES=43200
# YT_RANDOM_WINDOW_MINUTES=30
# YT_RANDOM_REGION_POOL=US,GB,JP,VN
# YT_RANDOM_QUERY_POOL=gaming,stream,highlights,,
# --- since-mode (non-random) ---
# YT_SINCE_MODE=local_midnight
# YT_LOCAL_TZ=America/Toronto
# YT_SINCE_MINUTES=10
```

---

## Worker — `worker/discover_once.py` (v4)

### Random Mode
```powershell
$env:YT_API_KEY="<KEY>"
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
$env:YT_RANDOM_MODE="1"
$env:YT_RANDOM_LOOKBACK_MINUTES="43200"   # 30 days
$env:YT_RANDOM_WINDOW_MINUTES="30"
$env:YT_RANDOM_REGION_POOL="US,GB,JP,VN"
$env:YT_RANDOM_QUERY_POOL="gaming,stream,highlights,,"
python .\worker\discover_once.py
```

### Since‑Mode (non‑random)
**Canada from local midnight (Toronto):**
```powershell
$env:YT_REGION="CA"
$env:YT_SINCE_MODE="local_midnight"
$env:YT_LOCAL_TZ="America/Toronto"
python .\worker\discover_once.py
```

**From the moment you press run (use with loop):**
```powershell
$env:YT_REGION="CA"
$env:YT_SINCE_MODE="now"
$env:DISCOVER_INTERVAL_SECONDS="30"
python .\worker\scheduler.py
```

**Safer window "last 10 minutes":**
```powershell
$env:YT_SINCE_MODE="minutes"
$env:YT_SINCE_MINUTES="10"
python .\worker\discover_once.py
```

> **Important**: Upserts by `_id` (videoId) make repeated runs idempotent; using a small, overlapping window while looping helps you not miss fresh videos due to API indexing delays.

---

## Runner Scripts

### `run_discover_loop.ps1` (recommended)
Runs the discover loop every **N seconds**:
```powershell
# If script is blocked:
# powershell -NoProfile -ExecutionPolicy Bypass -File .\run_discover_loop.ps1 -IntervalSeconds 30 -RandomMode $true

.\run_discover_loop.ps1 -IntervalSeconds 30 -RandomMode $true
# Params you can change:
# -ProjectRoot, -IntervalSeconds
# -RandomMode/-RandomLookbackMinutes/-RandomWindowMinutes
# -RandomRegionPool/-RandomQueryPool
# -Region/-LookbackMinutes/-Query/-ChannelId/-ChannelHandle/-TopicId/-CategoryId
# -MongoUri/-ApiKey
```
> **Do not** run this together with a separate `run_scheduler.ps1` — pick one.

### Autorun (Windows Scheduled Task)
Use the provided installer script to start the loop automatically:
```powershell
.\install_autorun_task.ps1       # installs a task “YT Discover Loop” (AtLogOn)
# To remove:
.\uninstall_autorun_task.ps1
```
**Run on boot (no login)**: create an AtStartup task with the **SYSTEM** account (see script comments).  
Place your API key in `.env` so the SYSTEM account can load it without relying on user env vars.

---

## Verify & Inspect
- Latest discovered:
  - `http://127.0.0.1:8000/videos?sort=discovered&order=desc&limit=20`
- Counts:
  - `http://127.0.0.1:8000/videos/count`
  - `http://127.0.0.1:8000/videos/count?status=tracking`
- Stats:
  - `http://127.0.0.1:8000/stats`
- Mongo quick check (PowerShell):
```powershell
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
python - << 'PY'
import os
from pymongo import MongoClient
db = MongoClient(os.environ["MONGO_URI"]).get_database()
print("total:", db.videos.count_documents({}))
for d in db.videos.find({}, {"_id":1}).sort([("tracking.discovered_at",-1)]).limit(5):
    print("-", d["_id"])
PY
```

---

## Troubleshooting
- **“not digitally signed” script error**:  
  `Unblock-File .\run_discover_loop.ps1` *or* `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
- **Emoji / charmap crash**: v4 already forces UTF‑8; ensure console is UTF‑8 if needed:
  ```powershell
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
  $env:PYTHONUTF8="1"; $env:PYTHONIOENCODING="utf-8"
  ```
- **No results**: start with broader `YT_REGION`, add a small lookback, avoid too many filters, and confirm your API quota.

---

## Next Steps
- Implement `track_once.py` to record view/like/comment snapshots until age ≥ 24h
- Docker Compose (api + worker + mongo)
- Indexes & score rules (e.g., basic “likely_viral” heuristics)