# YT AutoScanner — Initial Setup Guide

This guide explains how to install and set up **YT AutoScanner** from scratch on a new machine. This guide is for local machine (your laptop or PC)

---

## ✅ 1. Install Required Software
### Windows
- Install Python 3.10 or newer
- Install Git
- Install Docker Desktop (recommended for MongoDB)

---

## ✅ 2. Clone the Repository
```bash
git clone https://github.com/<your-repo>/yt-autoscanner.git
cd yt-autoscanner
```

---

## ✅ 3. Create a Virtual Environment

Run this command inside the project root folder to make a virtual environment (will not affect your system, everything will be installed to this folder in the project root folder):

```bash
python -m venv venv
# Activate on Windows
venv\Scripts\activate
```

---

## ✅ 4. Install Python Dependencies

Install all required libraries for the project into the **virtual environment (venv)** you just created. Make sure the venv is activated before running this command

```bash
pip install -r requirements-dev.txt
```

---

## ✅ 5. Set Up MongoDB

MongoDB can be installed **natively on Windows** without Docker.

### 5.1 Download MongoDB Community Server
Download from the official website:  
https://www.mongodb.com/try/download/community

Recommended options:
- **Edition:** Community Server  
- **Version:** 7.x (or latest stable)  
- **OS:** Windows x64  
- **Package:** MSI Installer  

### 5.2 Install MongoDB on Windows
Run the installer and during setup:

- ✅ Check **"Install MongoDB as a Service"**  
- ✅ Check **"Run service as Network Service user"** (default is fine)  
- ✅ (Optional) Check **"Install MongoDB Compass"** if you want a GUI  (for beginner and my team, it is important to view the data structure)
- ✅ (Recommended) Check **"Add MongoDB binaries to PATH"** if available  

After installation, MongoDB will run automatically as a Windows service


## ✅ 6. Create MongoDB Indexes

Before the system can run efficiently, you need to create MongoDB indexes. These indexes significantly speed up queries for discovery, tracking, and ML processes.

Make sure MongoDB is running, then execute:

```bash
python tools/make_indexes.py
```

---

## ✅ 7. Create `.env` Configuration File
Create a new file named `.env` in the project root. You can use notebook to create it, just save `.env` (no file name). Please note that you have to put Youtube API key (V3) in this file
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

---

## ✅ 8. Test Connectivity
### Test MongoDB (Windows)
Since Windows does not support Unix-style heredoc (<<EOF), use one of the methods below:

#### **Option A — Run a temporary Python script**
Create a file named `test_mongo.py` (I created one for you in the project root folder)
```python
from pymongo import MongoClient
c = MongoClient("mongodb://localhost:27017/")
print(c.list_database_names())
```
Run it:
```powershell
python test_mongo.py
```

#### **Option B — Use Python interactive mode**
```powershell
python
```
Then paste:
```python
from pymongo import MongoClient
print(MongoClient("mongodb://localhost:27017/").list_database_names())
```

---

### Test YouTube API

Please note that you have to put your youtube API key in the `.env` file

```bash
python worker/discover_once.py --test
```

---

## ✅ 9. Run the Dashboard

The dashboard provides a real-time interface for monitoring KPIs, video statistics, system activity, and ML flagging. Make sure your virtual environment is active, then launch the dashboard using:

```bash
streamlit run dashboard/app.py
```
Visit: `http://localhost:8501`

---

## ✅ 10. Run the Full System

To run the whole system, you could use the `run_local_loop.ps1` file in the project root folder

```bash
# Allow script execution
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Start discovery + tracking + ML loop
./run_local_loop.ps1
```

You could set up the time for all the workers

```
$DiscoverIntervalSeconds = 10     # seconds (discover new videos)
$TrackIntervalSeconds    = 5      # seconds (track the videos until it is removed or complete the cycle)
$LowQIntervalSeconds     = 1800   # seconds (Machine learning model for flagging low_quality videos, we have 2 models at the ts of 3h and the ts of 6h)
```

The loop performs:
```
discover → track → ML auto-flag → sleep → repeat
```

---

## Optional: Extra Dev Tools
Install tools for debugging and machine learning:
```bash
pip install jupyter
pip install xgboost scikit-learn
pip install pyarrow fastparquet
```

---

## After First Run
These folders will be generated automatically:
- `exports/` — exported Parquet/JSON
- `logs/` — worker logs

---

## 🎉 Setup Complete!
Your YT AutoScanner environment is ready for development, tracking, and ML training.

📅 **Last Updated:** **Nov 15 2025**