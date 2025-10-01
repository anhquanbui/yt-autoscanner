# yt-autoscanner (Local Dev – API + MongoDB)

Minimal starter for a YouTube video scanner system.  
This README covers **local development up to now**: run the FastAPI API, connect MongoDB, seed demo data, and test endpoints. (Workers for discover/track come next.)

---

## 1) Project Structure (current)
```
yt-autoscanner/
├─ api/
│  ├─ main.py                 # FastAPI app
│  └─ requirements.txt        # fastapi, uvicorn, pymongo
├─ venv/                      # Python virtual environment (local)
└─ README.md
```

**API endpoints (current):**
- `GET /health` – healthcheck
- `GET /videos?status=&limit=` – list videos (optionally filter by `tracking.status`)
- `GET /video/{id}` – get a single video by id
- `GET /tracking` – list videos being tracked (ordered by `next_poll_after`)
- `GET /complete` – list completed (>24h) videos

---

## 2) Prerequisites
- **Python 3.11+**
- **MongoDB** running locally:
  - EITHER install MongoDB Community Server (Windows: `winget install -e --id MongoDB.Server`)
  - OR run via Docker: `docker run -d --name mongo -p 27017:27017 mongo:7`  
    > If `docker` is not available on your machine, install Docker Desktop or use the native MongoDB Server above.

---

## 3) Create & Activate Virtual Environment
> On Windows PowerShell (recommended):

```powershell
# from project root
python -m venv venv

# Option A: (requires execution policy change once)
# Run PowerShell as Administrator once:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.env\Scripts\Activate.ps1

# Option B: without changing policy (CMD)
# venv\Scriptsctivate.bat
```

Verify:
```powershell
python --version
```

---

## 4) Install Dependencies (API)
```powershell
pip install -r api
equirements.txt
```

---

## 5) Configure Environment
Set `MONGO_URI` **in the same terminal** where you will run the API:

```powershell
# Local MongoDB default:
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
```

> On Linux/Mac: `export MONGO_URI="mongodb://localhost:27017/ytscan"`

---

## 6) Run the API
From the `api/` folder:

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open:
- Swagger UI: http://127.0.0.1:8000/docs  
- Health: http://127.0.0.1:8000/health

---

## 7) Seed Demo Data (so `/videos` shows something)
Open a **second terminal**, activate the same `venv`, set `MONGO_URI` again, then run one of the following:

### Method A: create `seed.py`
```powershell
$env:MONGO_URI="mongodb://localhost:27017/ytscan"

$code = @'
import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ytscan")
db = MongoClient(MONGO_URI).get_database()

docs = [
    {
        "_id":"DEMO123",
        "snippet":{"title":"Demo video 1","publishedAt":"2025-09-30T12:00:00Z"},
        "tracking":{"status":"tracking","discovered_at":"2025-10-01T00:00:00Z","next_poll_after":"2025-10-01T00:00:00Z","poll_count":0},
        "stats_snapshots":[],
        "ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}
    },
    {
        "_id":"DEMO456",
        "snippet":{"title":"Old video","publishedAt":"2025-09-29T12:00:00Z"},
        "tracking":{"status":"complete","discovered_at":"2025-09-29T13:00:00Z","next_poll_after":None,"poll_count":5,"stop_reason":"age>=24h"},
        "stats_snapshots":[{"ts":"2025-09-29T13:00:00Z","viewCount":100,"likeCount":3,"commentCount":0}],
        "ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}
    }
]
db.videos.insert_many(docs, ordered=False)
print("Seeded", len(docs), "docs")
'@
Set-Content -Path .\seed.py -Value $code -Encoding UTF8
python .\seed.py
```

### Method B: inline (PowerShell here-string)
```powershell
$env:MONGO_URI="mongodb://localhost:27017/ytscan"
@'
import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/ytscan")
db = MongoClient(MONGO_URI).get_database()

db.videos.insert_one({
    "_id":"DEMO123",
    "snippet":{"title":"Demo video","publishedAt":"2025-09-30T12:00:00Z"},
    "tracking":{"status":"tracking","discovered_at":"2025-10-01T00:00:00Z","next_poll_after":"2025-10-01T00:00:00Z","poll_count":0},
    "stats_snapshots":[],
    "ml_flags":{"likely_viral":False,"viral_confirmed":False,"score":0.0}
})
print("seeded ok")
'@ | python -
```

Now call:
- `GET /videos?limit=5`
- `GET /tracking`
- `GET /complete`

---

## 8) Troubleshooting

**Swagger shows “Failed to fetch” on `/videos`:**
- Make sure MongoDB is running (Windows service **MongoDB** or Docker container).
- Ensure `MONGO_URI` is set **in the same terminal** that runs Uvicorn.
- Restart the API after changing env vars (Ctrl+C then start again).

**PowerShell blocks venv activation (`Activate.ps1`)**
- Run once as Admin:  
  `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`  
  Or use `venv\Scriptsctivate.bat` in CMD.

**`docker` not recognized**
- Either install Docker Desktop *or* use MongoDB Community Server (no Docker needed).

**Confirm you are inside venv**
```powershell
python -c "import sys; print(sys.prefix)"
# should print ...\yt-autoscanner\venv\...
```

---

## 9) Next Steps (coming later)
- Add **workers** (`discover.py`, `track.py`) to pull new videos hourly and track stats until 24h.
- Package everything with **Docker Compose** (api + worker(s) + mongo).
- Add indexes and “likely_viral” rule.
