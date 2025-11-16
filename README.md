# 🚀 **YT AutoScanner — PRO README**
### _Early‑Signal YouTube Ingestion • Automated Tracking • ML‑Driven Filtering • Scalable Data Pipeline_

<p align="center">
  <img src="https://img.shields.io/badge/YouTube%20API-v3-red?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/MongoDB-7.x-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/ML-AutoFlag-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Status-Active-brightgreen?style=for-the-badge" />
</p>

---

# 🎯 **1. What is YT AutoScanner?**
**YT AutoScanner** is a full‑stack YouTube data ingestion and machine‑learning pipeline built for:
- 🔍 **Discovering new YouTube uploads** across regions/keywords
- 📈 **Tracking early engagement (0 → 24h)** with 1h milestone snapshots
- 🧮 **Feature engineering** for ML models
- 🤖 **Low‑quality auto‑flagging** using trained classifiers (3h/6h)
- 📦 **Mongo → Parquet export** for large‑scale ML training
- 🧊 **Scalable architecture** with workers, automation scripts, and dashboards

Originally developed as part of a **Data Analytics & AI research project** at **Saskatchewan Polytechnic**, now expanded into a production‑grade pipeline.

---

# ✨ **2. Key Highlights**
- ⚡ Ultra‑fast **video discovery engine**
- 🧩 Modular **worker architecture** (discover / track / ML)
- 🧠 ML‑powered **low-quality prediction** (XGBoost/Sklearn)
- 🚀 High‑performance **MongoDB indexes** for 100k+ documents
- 📊 Built‑in **Streamlit Dashboard** for KPIs & monitoring
- 🔧 Automated **PowerShell loop runner** for continuous ingestion
- 🔄 Fully supports retraining + model lifecycle

---

# ⚡ **3. Quick Start (5 Steps)**
```powershell
git clone https://github.com/anhquanbui/yt-autoscanner.git
cd yt-autoscanner
python -m venv venv
./venv/Scripts/Activate.ps1
pip install -r requirements-dev.txt
```

### Configuration
Add a `.env` file in your project root (please note to add your Youtube API Key V3)
```
MONGO_URI=mongodb://localhost:27017/ytscan
YOUTUBE_API_KEY=YOUR_YOUTUBE_API_KEY
EXPORT_DIR=./data_export

# --------- 3H MODEL ----------
LOWQ_MODEL_3H_PATH=models/low_quality/low_quality_model_3h.joblib
LOWQ_THRESHOLD_3H=0.294
LOWQ_3H_ENABLED=true
LOWQ_3H_ONLY_MISSING=true
LOWQ_3H_STOP_IF_LOW=true

# --------- 6H MODEL ----------
LOWQ_MODEL_6H_PATH=models/low_quality/low_quality_model_6h.joblib
LOWQ_THRESHOLD_6H=0.281
LOWQ_6H_ENABLED=true
LOWQ_6H_ONLY_MISSING=true
LOWQ_6H_STOP_IF_LOW=true
```

### Check MongoDB
```powershell
python test_mongo.py
```

### Create indexes for collections (your dataset)
```powershell
python tools/make_indexes.py
```

### Launch the unified runner
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
./run_both_local.ps1
```

---

# 🏗️ **4. Architecture Overview**
```
          +-------------------+
          | YouTube API (v3) |
          +---------+---------+
                    |
          +---------v---------+
          |  discover_once.py |
          +---------+---------+
                    |
          +---------v---------+
          |   track_once.py   |
          |  1h→24h snapshots |
          +---------+---------+

```

---

# 🧩 **5. Project Structure**
```
YT-AUTOSCANNER/
├── api/                              # FastAPI backend
│   ├── main.py                       # API entrypoint
│   └── __pycache__/                  # Python cache
│
├── config/                           # Shared configuration utilities
│   ├── __init__.py                   # Enables module imports
│   └── path_utils.py                 # Path + directory helpers
│
├── dashboard/                        # Streamlit monitoring dashboard
│   ├── app.py                        # Main Streamlit app
│   ├── components/                   # UI components
│   │   ├── db.py                     # MongoDB connection layer
│   │   └── __pycache__/              # Streamlit component cache
│   └── pages/                        # Dashboard pages
│       └── 01_Overview.py            # KPI overview page
│
├── data_export/                      # Exported Parquet/JSON for ML
│
├── docs/                             # Documentation for pipeline + workers
│   ├── 01_initial_setup_guide.md
│   ├── 02_collection_overview.md
│   ├── QA.md
│   ├── 
│   ├── 
│   ├── 
│   ├── 
│   ├── 
│   ├── 
│   ├── 
│   ├── 
│   └── 
│
├── logs/                             # Worker + system logs
│
├── models/                           # Machine learning model storage
│   └── low_quality/
│       ├── low_quality_model_3h.joblib   # 3h model
│       └── low_quality_model_6h.joblib   # 6h model
│
├── tools/                            # Maintenance + data-processing scripts
│   ├── __init__.py                   # Module init
│   ├── backfill_channels.py          # Fill missing channel data
│   ├── backfill_missing_fields.py    # Fix missing fields in DB
│   ├── index_maintenance.log         # Index check log
│   ├── make_indexes.py               # Create MongoDB indexes
│   ├── ml_flags_migrate.py           # Migrate ML flag schema
│   └── mongo_to_parquet.py           # Export Mongo → Parquet
│
├── worker/                           # Core data ingestion + tracking system
│   ├── __init__.py                   # Module init
│   ├── discover_once.py              # YouTube discovery worker
│   ├── low_quality_autoflag.py       # ML auto-flag worker
│   ├── process_data.py               # ML feature-engineering processor
│   └── track_once.py                 # Tracking worker (1h → 24h snapshots)
│
├── run_local_loop.ps1                # Unified discovery + tracking loop
├── run_track_one_loop_30s.ps1        # Track-only loop (debug)
│
├── seed.py                           # Test data generator
│
├── requirements.txt                  # Prod dependencies
├── requirements-dev.txt              # Dev dependencies
├── README.md                         # Main documentation
├── .env                              # Environment config (API keys, Mongo URI, Machine Learning configuration)
└── .gitignore                        # Git ignore rules

```

---

# ▶️ **6. Running the System**
## Run workers manually
```powershell
python worker/discover_once.py
python worker/track_once.py
python worker/low_quality_autoflag.py
```

## Run tools manually
```powershell
python tools/name_of_tool.py
or
python -m tools.name_of_tool # mainly for export tools, run if `python tools/...` does not work
```

## Unified Local Runner
```powershell
./run_both_local.ps1
```
Automatically executes:
- discovery cycle
- tracking cycle
- ML auto-flagging
- sleep → repeat

### Windows Authorization Fix
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
./run_both_local.ps1
```

---

# 📚 **7. Documentation Index**
Inside `docs/`:
- [01_initial_setup_guide.md](docs/01_initial_setup_guide.md)
- [02_collection_overview.md](docs/02_collection_overview.md)
- [QA.md](docs/QA.md) #For beginner and my team

---

# 📌 **8. Roadmap**
| Feature | Status |
|---------|--------|
| Viral prediction model | 🔄 In progress |
| Dashboard v2 (charts + alerts) | 🚧 Planned |
| Worker parallelization | 🚧 Planned |
| Export automation | 🔄 In progress |
| API expansion | 🔜 Next |

---

# ❤️ **9. Credits**
Developed by:
- **Anh Quan Bui** — System Architect / ML Engineer
- **Eneyi Simeni** — Data Engineer
- **Nguyen Ha Dung** — ML Developer

📅 **Last Updated:** **Nov 15 2025**
