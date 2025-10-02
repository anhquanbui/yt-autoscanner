# yt-autoscanner — Local Dev (API + MongoDB + YouTube Worker + Scheduler)

A minimal starter to ingest YouTube videos into **MongoDB** and expose them via a **FastAPI** API.  
This README covers: running the API, connecting MongoDB, seeding demo data, pulling real videos from YouTube (with @handle / topic / category filters), and scheduling the worker.

---

## Project Structure
```
yt-autoscanner/
├─ api/
│  ├─ main.py                  # FastAPI app: list videos, get by id
│  └─ requirements.txt         # fastapi, uvicorn, pymongo
├─ worker/
│  ├─ discover_once.py         # YouTube fetcher (v2: topic/category/@handle, fallback mostPopular)
│  └─ scheduler.py             # loop runner to call discover on an interval
├─ run_scheduler.ps1           # PowerShell helper to start scheduler
├─ venv/                       # Python virtual environment (local)
└─ README.md
```

**API endpoints (current):**
- `GET /health`
- `GET /videos?status=&limit=`
- `GET /video/{id}`
- `GET /tracking`
- `GET /complete`

---

## Prerequisites
- **Python 3.11+**
- **MongoDB** running locally  
  - Windows: `winget install -e --id MongoDB.Server` (service **MongoDB** on `27017`)  
  - or Docker: `docker run -d --name mongo -p 27017:27017 mongo:7`
- A **YouTube Data API v3** key (Google Cloud Console)

> Security tip: never commit your API key. Rotate it if it was exposed.

---

## Setup
### 1) Create & activate a virtual environment
PowerShell:
```powershell
# from project root
python -m venv venv

# If PowerShell blocks scripts, run once as Admin:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.venv\Scripts\Activate.ps1
```
Verify:
```powershell
python --version
```

### 2) Install dependencies
```powershell
pip install -r api
equirements.txt
# If worker has a separate requirements file, also:
# pip install -r worker
equirements.txt
```

### 3) Configure Mongo connection
Set `MONGO_URI` **in the same terminal** that runs the API:
```powershell
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
```

### 4) Run the API
From the `api/` folder:
```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Open:
- Swagger: http://127.0.0.1:8000/docs  
- Health:  http://127.0.0.1:8000/health

---

## Seed Demo Data (optional)
Open a **second terminal**, activate the same `venv`, set `MONGO_URI` again, then:
```powershell
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
$code = @'
import os
from pymongo import MongoClient

db = MongoClient(os.environ.get("MONGO_URI")).get_database()
docs = [
  {"_id":"DEMO123","snippet":{"title":"Demo video 1","publishedAt":"2025-09-30T12:00:00Z"},"tracking":{"status":"tracking","discovered_at":"2025-10-01T00:00:00Z","next_poll_after":"2025-10-01T00:00:00Z","poll_count":0},"stats_snapshots":[],"ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}},
  {"_id":"DEMO456","snippet":{"title":"Old video","publishedAt":"2025-09-29T12:00:00Z"},"tracking":{"status":"complete","discovered_at":"2025-09-29T13:00:00Z","next_poll_after":None,"poll_count":5,"stop_reason":"age>=24h"},"stats_snapshots":[{"ts":"2025-09-29T13:00:00Z","viewCount":100,"likeCount":3,"commentCount":0}],"ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}}
]
db.videos.insert_many(docs, ordered=False)
print("seeded", len(docs), "docs")
'@
Set-Content -Path .\seed.py -Value $code -Encoding UTF8
python .\seed.py
```
Check: `GET /videos?limit=5`

---

## YouTube Worker — `worker/discover_once.py` (v2)
Pulls recent videos via `search.list` and upserts them into MongoDB (idempotent by `videoId`).

**Features**
- Filters: `YT_QUERY`, `YT_CHANNEL_ID`, **`YT_CHANNEL_HANDLE`** (auto resolves to channelId), `YT_TOPIC_ID` (e.g. Gaming `/m/0bzvm2`).
- **Category filtering**: after search, fetch details with `videos.list` and keep only `snippet.categoryId == YT_FILTER_CATEGORY_ID` (e.g. `20`).
- **Fallback**: if search returns 0 and a category is set, fetch `videos.list?chart=mostPopular` to ensure data.
- `YT_LOOKBACK_MINUTES`: only include videos published after `now - minutes`.

**Environment variables**
- `YT_API_KEY` *(required)* — YouTube Data API v3 key
- `MONGO_URI` *(default `mongodb://localhost:27017/ytscan`)*
- `YT_REGION` *(default `US`)*
- `YT_QUERY` *(optional)* — text query, e.g. `gaming`
- `YT_CHANNEL_ID` *(optional)*
- `YT_CHANNEL_HANDLE` *(optional)* — `@handle`
- `YT_TOPIC_ID` *(optional)* — e.g. `/m/0bzvm2` for Gaming
- `YT_FILTER_CATEGORY_ID` *(optional)* — e.g. `20` for Gaming
- `YT_LOOKBACK_MINUTES` *(default `360`)* — minutes to look back for recent uploads

**Examples (PowerShell)**
- Wide 24h pull by keyword:
  ```powershell
  $env:YT_API_KEY="<YOUR_KEY>"
  $env:MONGO_URI="mongodb://localhost:27017/ytscan"
  $env:YT_REGION="US"
  $env:YT_LOOKBACK_MINUTES="1440"
  $env:YT_QUERY="gaming"
  Remove-Item Env:YT_CHANNEL_HANDLE,Env:YT_CHANNEL_ID,Env:YT_FILTER_CATEGORY_ID,Env:YT_TOPIC_ID -ErrorAction SilentlyContinue
  python .\worker\discover_once.py
  ```
- Single channel via @handle (no category filter):
  ```powershell
  $env:YT_CHANNEL_HANDLE="@SomeChannel"
  Remove-Item Env:YT_FILTER_CATEGORY_ID,Env:YT_QUERY,Env:YT_TOPIC_ID -ErrorAction SilentlyContinue
  $env:YT_REGION="US"
  $env:YT_LOOKBACK_MINUTES="10080"
  python .\worker\discover_once.py
  ```
- Strict Gaming category (20) with fallback to mostPopular:
  ```powershell
  $env:YT_REGION="GB"
  $env:YT_FILTER_CATEGORY_ID="20"
  $env:YT_LOOKBACK_MINUTES="43200"
  Remove-Item Env:YT_QUERY,Env:YT_CHANNEL_ID,Env:YT_CHANNEL_HANDLE,Env:YT_TOPIC_ID -ErrorAction SilentlyContinue
  python .\worker\discover_once.py
  ```

Open: `http://127.0.0.1:8000/videos?limit=20` or `/video/<videoId>`.

---

## Simple Scheduler (every 30s for testing)
Run the worker on a fixed interval and store logs.

**Inline**
```powershell
# ensure env (API key, Mongo, etc.) is set in this terminal
$env:DISCOVER_INTERVAL_SECONDS="30"
python .\worker\scheduler.py
# logs: .\logs\discover-YYYYMMDD.log
```

**PowerShell helper**
```powershell
# From project root
.
un_scheduler.ps1 -IntervalSeconds 30
```
> Windows Task Scheduler minimum trigger is 1 minute; `run_scheduler.ps1` keeps a 30s internal loop.

**Quota note:** 30s is only for testing. For production, consider a 5–10 minute interval and a smaller `YT_LOOKBACK_MINUTES` (e.g., `90–180`).

---

## Troubleshooting
- **Swagger “Failed to fetch”**: ensure MongoDB is running; set `MONGO_URI` in the same terminal; restart Uvicorn.
- **PowerShell cannot run `Activate.ps1`**: run once as Admin  
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`  
  or use `venv\Scripts\activate.bat` from CMD.
- **`found=0` with search**: region/filters too tight or lookback too small. Increase `YT_LOOKBACK_MINUTES`, remove filters, or rely on fallback `mostPopular`.
- **`upserted=0` with `found>0`**: those videos were already present (upsert skips duplicates).
- **Key security**: rotate your API key if it was exposed.

---

## Next
- Add `track_once.py` to snapshot `statistics` (views/likes/comments) periodically until 24h, then mark `complete`.
- Docker Compose (`api + worker + mongo`) with `.env` configuration.
- Mongo indexes for faster queries.
