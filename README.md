
# yt-autoscanner — Local Dev (API + MongoDB + YouTube Worker + Scheduler)

A minimal starter to ingest **YouTube** videos into **MongoDB** and expose them via a **FastAPI** API.
This README covers local development, environment config, the discover worker (v4.1), and the Windows loop runner.

> **Quick links**
> - API (local): `http://127.0.0.1:8000/docs`
> - Mongo (local): `mongodb://localhost:27017/ytscan` (`videos` collection)
> - Logs (local): `./logs/discover-YYYYMMDD.log`

---

## What's new (since previous README)

### Worker (`discover_once.py`) — **v4.1**
- **Quota-aware:** returns **exit code 88** on `quotaExceeded/dailyLimitExceeded/rateLimitExceeded` → the scheduler stops cleanly.
- **`YT_MAX_PAGES`**: hard cap number of `search.list` pages per run to save quota.
- Kept from v4: Random Mode (regions/queries, random time slice), SINCE_MODE (`lookback`/`now`/`minutes`/`local_midnight`), `.env` support, UTF‑8 safe logging, `@handle → channelId`, category filter (+ fallback to `mostPopular`).

### Scheduler (`worker/scheduler.py`) — **v2**
- Loops `discover_once.py` every `DISCOVER_INTERVAL_SECONDS` seconds.
- **Stops** when worker exits with **code 88** (quota exhausted).

### Windows Runner (`run_discover_loop.ps1`) — **v4**
- **Single‑instance lock** (prevents duplicate loops).
- Logs via **StreamWriter** with `FileShare.ReadWrite` (no file‑locking error while tailing).
- Auto‑activate `venv`, force UTF‑8, create `./logs/`, start `worker/scheduler.py` (unbuffered).
- You can pass overrides via parameters or rely on `.env`.

> **Important**
> - Keep your **API key out of git** — store it in `.env` at project root.
> - Run **only one** discover loop at a time to avoid burning quota.
> - If you deploy with Docker or cron/systemd, **don’t** run the PowerShell loop in parallel.

---

## Project structure

```
yt-autoscanner/
├─ api/
│  ├─ main.py                   # FastAPI app
│  └─ requirements.txt
├─ worker/
│  ├─ discover_once.py          # v4.1 (quota-aware + YT_MAX_PAGES)
│  └─ scheduler.py              # v2 (stop on exit code 88)
├─ run_discover_loop.ps1        # v4 (single-instance + safe logging)
├─ venv/                        # local Python virtual env (optional)
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

### `/videos` query params
- `status`: `tracking` | `complete`
- `q`: case-insensitive substring on `snippet.title` / `snippet.channelTitle`
- `channel_id`: filter by `snippet.channelId`
- `since`: ISO 8601 or relative (`PT2H`, `30m`) → `publishedAt >= since`
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
/videos/count?status=tracking&q=gaming
/stats
```

---

## Local setup

### 1) Python & venv (Windows PowerShell)
```powershell
python -m venv venv
# If blocked once:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\venv\Scripts\Activate.ps1
```

### 2) Install dependencies
```powershell
pip install -r api/requirements.txt
# If worker has its own file, also: pip install -r worker/requirements.txt
```

### 3) MongoDB
- Install local **MongoDB Community** OR
- Use Docker: `docker run -d --name mongo -p 27017:27017 mongo:7`
- Connection string (local): `mongodb://localhost:27017/ytscan`

### 4) Run API
```powershell
cd api
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# Swagger: http://127.0.0.1:8000/docs
```

---

## Environment (`.env` at project root)

Create `.env` (and add `.env` to `.gitignore`):

```
# --- Required ---
YT_API_KEY=YOUR_YOUTUBE_API_KEY
MONGO_URI=mongodb://localhost:27017/ytscan

# --- Common ---
YT_REGION=US
YT_LOOKBACK_MINUTES=360

# --- Random mode ---
# YT_RANDOM_MODE=1
# YT_RANDOM_LOOKBACK_MINUTES=43200
# YT_RANDOM_WINDOW_MINUTES=30
# YT_RANDOM_REGION_POOL=US,GB,JP,VN
# YT_RANDOM_QUERY_POOL=gaming,stream,highlights,,

# --- Since-mode (non-random) ---
# YT_SINCE_MODE=lookback        # lookback | now | minutes | local_midnight
# YT_SINCE_MINUTES=10
# YT_LOCAL_TZ=America/Toronto   # required if using local_midnight

# --- Quota safety ---
# YT_MAX_PAGES=2                # cap pages per run; 1–3 recommended in loops
# DISCOVER_INTERVAL_SECONDS=60  # loop interval for scheduler / PS runner
```

---

## Worker usage

### One‑shot (manual)
```powershell
# rely on .env
python .\worker\discover_once.py
```

### Loop (PowerShell runner, recommended on Windows)
```powershell
# Run from project root:
.
un_discover_loop.ps1 -IntervalSeconds 60 -RandomMode $true
# Notes:
# - Single-instance lock prevents duplicates
# - Logs at .\logs\discover-YYYYMMDD.log
# - Uses venv automatically if present
```

### Loop (Python scheduler directly)
```powershell
$env:DISCOVER_INTERVAL_SECONDS="60"
python .\worker\scheduler.py
```

### Quota exhaustion behavior
- When the worker hits quota, it prints a message and exits with **code 88**.
- The scheduler detects code 88 and **stops** the loop.
- To continue: update `YT_API_KEY` in `.env` and **start the loop again**.

*(Alternative: change scheduler to sleep until Pacific midnight and resume.)*

---

## Logs & monitoring

**Tail log (PowerShell):**
```powershell
$log = Join-Path (Get-Location) "logs\discover-$(Get-Date -Format yyyyMMdd).log"
Get-Content $log -Wait -Tail 200
```

**Check running loops (avoid duplicates):**
```powershell
Get-CimInstance Win32_Process |
  ? { $_.CommandLine -match 'yt-autoscanner.*(scheduler\.py|discover_once\.py)' } |
  select ProcessId, CommandLine
```

**Stop stray loops:**
```powershell
Stop-Process -Id <PID1>,<PID2> -Force
```

---

## MongoDB quick look

**Compass:** connect to `mongodb://localhost:27017/ytscan`, open `videos` collection.

**Count & sample (PowerShell):**
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

Filters in Compass:
- Tracking only: `{"tracking.status":"tracking"}`
- Latest discovered: sort by `{"tracking.discovered_at": -1}`
- Latest published: sort by `{"snippet.publishedAt": -1}`

---

## Domain & reverse proxy (prod)

With `youtube.example.com` on a separate VPS from your main WordPress site:
- DNS: `A youtube → <VPS IP>` (specific record overrides wildcard).
- Reverse proxy (Nginx/Caddy) forwards `youtube.example.com` → `127.0.0.1:8000`.
- If you want the API under `/api`, run uvicorn with `--root-path /api` and proxy `/api/`.

**CORS (if calling from another site):**
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://example.com", "https://youtube.example.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Quota best practices

- Prefer **small overlapping windows** in loops (`SINCE_MODE=minutes`, `YT_SINCE_MINUTES=10`).
- Keep `YT_MAX_PAGES` low (1–3) to reduce `search.list` calls (100 units each).
- Narrow results with `YT_REGION`, `YT_QUERY`, `YT_CHANNEL_ID/handle`, `YT_TOPIC_ID`.
- Avoid running multiple loops at once (PowerShell + cron + Docker).

---

## Next steps

- Implement `track_once.py` to collect stats until age ≥ 24h, then mark `tracking.status="complete"`.
- Add Docker Compose (api + worker + mongo) for Ubuntu VPS.
- Add indexes and ML heuristics (e.g., `likely_viral`).