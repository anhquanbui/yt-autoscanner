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
git clone https://github.com/yourrepo/yt-autoscanner.git
cd yt-autoscanner
python -m venv venv
./venv/Scripts/Activate.ps1
pip install -r requirements-dev.txt
```

### Start MongoDB (Docker)
```powershell
docker run -d --name ytscan-mongo -p 27017:27017 mongo:7
python tools/make_indexes.py
```

### Launch the unified runner
```powershell
./run_both_local.ps1
```

---

# ⚙️ **4. Configuration**
Add a `.env` file in your project root:
```
MONGO_URI=mongodb://localhost:27017/ytscan
YOUTUBE_API_KEY=YOUR_YOUTUBE_API_KEY
EXPORT_DIR=D:/YT-EXPORTS
```

---

# 🏗️ **5. Architecture Overview**
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
                    |
          +---------v---------+
          | process_data.py   |
          | Feature Engineering|
          +---------+---------+
                    |
          +---------v---------+
          | Parquet Export    |
          +---------+---------+
                    |
          +---------v---------+
          |  ML Models (6h)   |
          +-------------------+
```

---

# 🧩 **6. Project Structure (PRO Version)**
```
api/                → FastAPI backend
dashboard/          → Streamlit analytics UI
tools/              → Indexing, exports, migrations
worker/             → Discovery, tracking, ML auto-flagging
docs/               → Full documentation set
models/             → Saved ML models (joblib)
exports/            → Auto-generated parquet/json outputs
```

---

# ▶️ **7. Running the System**
## Start API
```powershell
cd api
uvicorn main:app --reload
```

## Run workers manually
```powershell
python worker/discover_once.py
python worker/track_once.py
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
Unblock-File -Path ./run_both_local.ps1
```

---

# 🤖 **8. ML Workflow**
### 1. Generate ML features
```powershell
python worker/process_data.py
```

### 2. Export MongoDB → Parquet
```powershell
python tools/mongo_to_parquet.py --chunk 50000
```

### 3. Train model (example: XGBoost)
```python
import xgboost as xgb
```

### 4. Save model → /models
### 5. Auto‑flag videos
```powershell
python worker/low_quality_autoflag.py
```

---

# 📚 **9. Documentation Index**
Inside `docs/`:
- discover_once.md
- track_once.md
- make_indexes_v3.md
- mongo_to_parquet_guide.md
- explanation_processed_videos.md
- pipeline_overview.md
- Autorun_Scripts_Guide.md

---

# 📌 **10. Roadmap**
| Feature | Status |
|---------|--------|
| Viral prediction model | 🔄 In progress |
| Dashboard v2 (charts + alerts) | 🚧 Planned |
| Worker parallelization | 🚧 Planned |
| Export automation | 🔄 In progress |
| API expansion | 🔜 Next |

---

# 📝 **11. License**
MIT / Apache‑2.0 / or your preferred license.

---

# ❤️ **12. Credits**
Developed by:
- **Anh Quan Bui** — System Architect / ML Engineer
- **Eneyi Simeni** — Data Engineer
- **Nguyen Ha Dung** — ML Developer

