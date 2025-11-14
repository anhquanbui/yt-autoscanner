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
| 📈 [track_once.md](docs/track_once.md) | Collects early engagement time-series snapshots |

### 🗄️ Database Schema & Performance
| File | Description |
|------|-------------|
| 🧩 [ytscan_collections_overview.md](docs/ytscan_collections_overview.md) | Database schema & relations (hot + cold storage) |
| 🚀 [make_indexes_v3.md](docs/make_indexes_v3.md) | Performance-optimized MongoDB indexes |

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
## What's New (Nov 13, 2025)

- Added Streamlit dashboard (`app.py`, `db.py`, `01_Overview.py`)
- Updated `run_local_loop.ps1` to run: discover + track + low-quality ML
- Added theme styles (1/2/3) with auto-colored pie chart
- Cleaned up UI layout and removed unused sidebar options

---
## What's new (Nov 10 2025)
- **Data Export System Added** — Introduced `path_utils.py` for centralized export path management, `mongo_to_parquet.py` for Parquet export, and `process_data.py` integration.

## What's new (Oct 31 2025)
- **process_data.py (v7.3)** — Refactored to remove dashboard JSON outputs, streamlined processed_status logic, added per-segment extended features (min_view, max_view, view_range, low_activity, plateau).
- **make_indexes.py (v4)** — Comprehensive index manager for videos and processed_videos, added partial + wildcard indexes for analytics/ML queries, safer idempotent index maintenance.

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

FULL CHANGELOG HERE --> [CHANGELOG.md](CHANGELOG.md)

## Project structure
```
YT-AUTOSCANNER/
├─ api/                             # FastAPI backend (optional)
│   ├─ main.py
│   └─ requirements.txt
│
├─ dashboard/                       # Streamlit Dashboard UI
│   ├─ app.py                       # Main dashboard entry
│   └─ pages/
│       ├─ 01_Overview.py           # Overview KPIs & charts
│       └─ (more pages added later)
│
docs/
├─ Autorun_Scripts_Guide.md         # Guide for automatic script runners
├─ backfill_channels.md             # Rebuild the channels collection
├─ discover_once.md                 # How discover_once collects new videos
├─ explanation_processed_videos.md  # Structure of processed_videos collection
├─ make_indexes_v3.md               # Required MongoDB indexes (v3)
├─ mongo_to_parquet_guide.md        # Export Mongo → Parquet for ML
├─ mongodb_setup_for_beginners.md   # MongoDB setup tutorial for beginners
├─ pipeline_overview.md             # Full pipeline architecture overview
├─ process_data_v6_usage.md         # How to use process_data v6
├─ QA.md                            # Frequently asked questions
├─ track_once.md                    # How track_once polls stats 0h→24h
└─ ytscan_collections_overview.md   # Overview of all MongoDB collections
│
├─ tools/                           # Maintenance & export tools
│   ├─ backfill_channels.py
│   ├─ backfill_missing_fields.py
│   ├─ make_indexes.py
│   ├─ mongo_to_parquet.py
│   ├─ ml_flags_migrate.py
│   └─ requirements.txt
│
├─ worker/                          # Core ingestion pipeline
│   ├─ discover_once.py             # Discover videos (0h)
│   ├─ track_once.py                # Track stats (0 → 24h)
│   ├─ low_quality_autoflag.py      # ML worker for low-quality filtering
│   ├─ process_data.py              # Convert DB → ML features
│   └─ requirements.txt
│
├─ models/                          # Machine Learning models
│   └─ low_quality/                 # Low-quality classifier
│       ├─ low_quality_model_6h.joblib
│       └─ README.md                # Info about training & usage
│
├─ exports/                         # Output data (generated automatically)
│   └─ *.parquet / *.json
│
├─ logs/                            # Worker/API logs (auto-rotated)
│   └─ *.log
│
├─ seed.py                          # Sample initializer
├─ CHANGELOG.md                     # Version history
├─ README.md                        # Main documentation
├─ .env                             # Secrets & environment configs
│
├─ run_local_loop.ps1               # Local continuous loop runner
└─ run_track_one_loop_30s.ps1       # run track_once every 30 seconds

```

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
Via Compass — guide → [mongodb_setup_for_beginners.md](docs/mongodb_setup_for_beginners.md)

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
# Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass (run if FullyQualifiedErrorId : UnauthorizedAccess)
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

## 🧩 New Additions — Data Export & ML Integration (Nov 2025)

### 🧭 Centralized Path Management — `config/path_utils.py`
Manages all export/output paths in one place.

#### Features
- Automatically loads `.env` configuration.
- Priority order:
  1. `EXPORT_DIR` (environment variable)
  2. `OUTPUT_DIR` (environment variable)
  3. Default fallback: `<project_root>/data_export/`
- Automatically creates the directory if missing.

#### Example
```python
from config.path_utils import get_export_dir
export_dir = get_export_dir()
```

Set this in `.env`:
```env
EXPORT_DIR=D:\PYTHON\PROJECT\yt-autoscanner\data_export #This is my PATH
```
If not set, defaults to `yt-autoscanner/data_export/`.

---

### 🧮 Updated Data Processor — `worker/process_data.py`
Refactored to use centralized `get_export_dir()`.

```python
from config.path_utils import get_export_dir

if args.out_dir:
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
else:
    out_dir = get_export_dir()
```
All processed outputs (JSON, CSV, etc.) are stored in the unified export directory.

---

### 🧾 Mongo Export Tool — `tools/mongo_to_parquet.py`
A new tool for exporting MongoDB collections to ML-ready Parquet files.

#### Highlights
- Reads `.env` (`MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`).
- Chunked writing — prevents memory overflow.
- Optional `--query`, `--limit`, `--chunk`, `--out` flags.
- Converts BSON to safe JSON types.
- Unified output path via `get_export_dir()`.

#### Example usage
```bash
python tools/mongo_to_parquet.py \
  --query '{"processed_status": "complete"}' \
  --limit 200000 \
  --chunk 50000 \
  --out processed_complete.parquet
```
Output files are written to `data_export/` or your `EXPORT_DIR` path.

FULL GUIDE: [mongo_to_parquet_guide.md](docs/mongo_to_parquet_guide.md)

---

### 📦 Updated Dependencies
Added to `requirements-dev.txt`:
```bash
pyarrow>=17.0.0
fastparquet>=2024.5.0
odfpy>=1.4.1
tqdm>=4.66.5
```
Install:
```bash
pip install -r requirements-dev.txt
```

---

### 🧬 Typical ML Workflow
1. Run `worker/process_data.py` to preprocess data.
2. Run `tools/mongo_to_parquet.py` to export MongoDB data.
3. Train ML models (e.g., XGBoost, sklearn) on the Parquet file.

---

## ✅ Advantages of Unified Export System
| Feature | Description |
|----------|--------------|
| **Centralized config** | All export paths defined in `.env` |
| **Cross-platform** | Works on Windows, Linux, Docker |
| **Chunked export** | Handles large datasets efficiently |
| **ML-ready** | Outputs Parquet files compatible with pandas/XGBoost |

---

📅 **Last Updated:** **Nov 10 2025**