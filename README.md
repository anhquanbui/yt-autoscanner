# yt-autoscanner — Local Dev (API + MongoDB + YouTube Worker + Scheduler)

Minimal starter to scan YouTube videos into MongoDB and expose them via a FastAPI API.  
This README reflects the **current state** you've built: run API, connect MongoDB, seed demo, pull real videos from YouTube (with @handle / topic / category filters), and run a simple 30s scheduler.

---

## 0) Project Structure (now)
```
yt-autoscanner/
├─ api/
│  ├─ main.py                  # FastAPI app (list videos, get by id)
│  └─ requirements.txt         # fastapi, uvicorn, pymongo
├─ worker/
│  ├─ discover_once.py         # YouTube fetcher (v2: topic/category/@handle, fallback mostPopular)
│  └─ scheduler.py             # loop runner to call discover on interval
├─ run_scheduler.ps1           # PowerShell helper to start scheduler
├─ venv/                       # Python virtual environment (local)
└─ README.md
```

Current API endpoints:
- `GET /health`
- `GET /videos?status=&limit=`
- `GET /video/{id}`
- `GET /tracking`
- `GET /complete`

---

## 1) Prerequisites
- **Python 3.11+**
- **MongoDB** running locally  
  - Windows: `winget install -e --id MongoDB.Server` (service **MongoDB** on `27017`)  
  - OR Docker: `docker run -d --name mongo -p 27017:27017 mongo:7`
- A **YouTube Data API v3** key (Google Cloud Console)

> **Security tip:** never commit your API key; rotate it if it leaked.

---

## 2) Create & Activate Virtual Environment
PowerShell (recommended):
```powershell
# from project root
python -m venv venv

# If PowerShell blocks scripts, run once as Admin:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.env\Scripts\Activate.ps1
```
Verify:
```powershell
python --version
```

---

## 3) Install Dependencies
```powershell
pip install -r api
equirements.txt
pip install -r worker
equirements.txt   # if you keep a separate file; otherwise requests+pymongo are already installed
```

---

## 4) Configure Environment (Mongo)
Set `MONGO_URI` **in the same terminal** that runs Uvicorn:
```powershell
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
```

---

## 5) Run the API
From the `api/` folder:
```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Open:
- Swagger: http://127.0.0.1:8000/docs  
- Health:  http://127.0.0.1:8000/health

---

## 6) (Optional) Seed Demo Data
**Terminal #2** (activate the same venv, set `MONGO_URI` again), create `seed.py`:
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

## 7) YouTube Worker – `worker/discover_once.py` (v2)
Features:
- Pull recent videos with `search.list` over a look-back window
- **Filters**: `YT_QUERY`, `YT_CHANNEL_ID`, **`YT_CHANNEL_HANDLE`**, `YT_TOPIC_ID` (e.g., Gaming `/m/0bzvm2`)
- **Category filter**: after search, call `videos.list` and keep only `snippet.categoryId==YT_FILTER_CATEGORY_ID` (e.g., `20` for Gaming)
- **Fallback**: if search returns 0 and you set a category, fetch `videos.list?chart=mostPopular` to ensure data
- Idempotent upsert by `_id=videoId`

### Environment variables
- `YT_API_KEY` (required) – Your YouTube Data API v3 key
- `MONGO_URI` (default `mongodb://localhost:27017/ytscan`)
- `YT_REGION` (default `US`) – affects search results / mostPopular
- `YT_QUERY` (optional) – text query (e.g., `gaming`)
- `YT_CHANNEL_ID` (optional) – restrict to a channel
- `YT_CHANNEL_HANDLE` (optional) – `@handle`, auto-resolves to channelId
- `YT_TOPIC_ID` (optional) – e.g. Gaming `/m/0bzvm2`
- `YT_FILTER_CATEGORY_ID` (optional) – e.g. `20` (Gaming)
- **`YT_LOOKBACK_MINUTES`** (default `360`) – window size: *search recent videos published after `now - minutes`*

> `YT_LOOKBACK_MINUTES` càng lớn → khả năng có kết quả càng cao, nhưng đi nhiều trang hơn (tốn quota). Dữ liệu trùng sẽ không bị chèn lại vì dùng upsert theo videoId.

### Example runs (PowerShell)

**A. Quét rộng 24h theo từ khoá “gaming” (không siết channel/category):**
```powershell
$env:YT_API_KEY="<YOUR_KEY>"
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
$env:YT_REGION="US"
$env:YT_LOOKBACK_MINUTES="1440"
$env:YT_QUERY="gaming"
Remove-Item Env:YT_CHANNEL_HANDLE,Env:YT_CHANNEL_ID,Env:YT_FILTER_CATEGORY_ID,Env:YT_TOPIC_ID -ErrorAction SilentlyContinue
python .\worker\discover_once.py
```

**B. Quét 1 kênh qua @handle (không lọc category):**
```powershell
$env:YT_CHANNEL_HANDLE="@MrBeastGaming"  # ví dụ
Remove-Item Env:YT_FILTER_CATEGORY_ID,Env:YT_QUERY,Env:YT_TOPIC_ID -ErrorAction SilentlyContinue
$env:YT_REGION="US"
$env:YT_LOOKBACK_MINUTES="10080"        # 7 ngày
python .\worker\discover_once.py
```

**C. Lọc đúng category Gaming (20) + fallback mostPopular:**
```powershell
$env:YT_REGION="GB"
$env:YT_FILTER_CATEGORY_ID="20"
$env:YT_LOOKBACK_MINUTES="43200"   # 30 ngày (backfill)
Remove-Item Env:YT_QUERY,Env:YT_CHANNEL_ID,Env:YT_CHANNEL_HANDLE,Env:YT_TOPIC_ID -ErrorAction SilentlyContinue
python .\worker\discover_once.py
```

Kiểm tra kết quả:
- `http://127.0.0.1:8000/videos?limit=20`
- `http://127.0.0.1:8000/video/<videoId>` (id in log)

---

## 8) Simple Scheduler (every 30s for testing)
We provide a tiny loop scheduler to re-run discover periodically.

**Option 1 – Run inline (manual):**
```powershell
# env from above (API key, Mongo, etc.) must be set in this terminal
$env:DISCOVER_INTERVAL_SECONDS="30"
python .\worker\scheduler.py
# Logs in .\logs\discover-YYYYMMDD.log
```

**Option 2 – PowerShell helper (Task Scheduler-friendly):**
```powershell
# From project root
.
un_scheduler.ps1 -IntervalSeconds 30
```
This script activates the venv, sets env vars, and starts the loop.  
> Windows Task Scheduler minimum trigger is 1 minute; `run_scheduler.ps1` keeps a **30s internal loop** for finer granularity.

**Quota note:** 30s is only for testing. In production consider 5–10 minutes, and use smaller `YT_LOOKBACK_MINUTES` (e.g., 90–180).

---

## 9) Troubleshooting
- **Swagger “Failed to fetch”**: ensure Mongo is running; set `MONGO_URI` in the same terminal; restart Uvicorn.
- **PowerShell cannot run Activate.ps1**: run once as Admin  
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`  
  or use `venv\Scriptsctivate.bat` from CMD.
- **`found=0` with search**: region+filters too tight or lookback too small. Increase `YT_LOOKBACK_MINUTES`, remove filters, or rely on fallback `mostPopular`.
- **`upserted=0` but `found>0`**: those videos already exist in DB.
- **API key security**: rotate your key if exposed.

---

## 10) Next
- Add `track_once.py` to snapshot `statistics` (views/likes/comments) periodically until 24h, then mark `complete`.
- Docker Compose (`api + worker + mongo`), with `.env` for configuration.
- Mongo indexes for faster queries.
