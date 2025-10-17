# process_data.py — Data Processing & Mongo Upsert Guide

This document explains how to use the `process_data.py` script to process YouTube tracking data from MongoDB and export it into JSON or Mongo collections.

---

## 🧩 Overview

The script processes tracked YouTube video statistics (from `videos` collection) into two outputs:
- `processed_videos.json` — Detailed per-video metrics including 1h, 3h, 6h, 12h, and 24h horizons.
- `dashboard_summary.json` — Compact overview for dashboard visualization.

Both files are automatically **upserted into MongoDB** by default.

---

## ⚙️ Features

- Auto-loads environment variables from `.env`
- Reads raw data from MongoDB or JSON file
- Cleans and summarizes view, like, and comment data
- Calculates coverage and milestone reach for each video
- Automatically upserts results into MongoDB (`processed_videos`, `dashboard_summary`)
- Supports optional query filters (`--query`)
- Can disable Mongo upsert if needed (`--no-mongo`)

---

## 📦 Requirements

Install dependencies:
```bash
pip install pymongo python-dotenv
```

Ensure `.env` exists in your project root:
```env
YT_API_KEY=YOUR_YOUTUBE_API_KEY
MONGO_URI=mongodb://localhost:27017/ytscan
```

---

## 🚀 Usage

### 1️⃣ Default (auto push to Mongo)
Automatically reads from `.env`, processes all videos, and upserts results:
```bash
python process_data.py
```

**Result:**
- Creates `processed_videos.json` and `dashboard_summary.json`
- Upserts both into MongoDB (`processed_videos`, `dashboard_summary`)

### 2️⃣ Disable Mongo upsert
If you only want JSON files without updating Mongo:
```bash
python process_data.py --no-mongo
```

### 3️⃣ Filter subset of videos
Process only specific documents using Mongo query (JSON format):
```bash
python process_data.py --query '{"tracking.status":"complete"}'
```

Examples:
```bash
python process_data.py --query '{"snippet.publishedAt":{"$gte":"2025-10-17T00:00:00Z"}}'
python process_data.py --query '{"source.regionCode":"US"}'
```

### 4️⃣ Custom collection names
To write results to different Mongo collections:
```bash
python process_data.py --out-coll-processed processed_videos_v2 --out-coll-summary dashboard_summary_v2
```

### 5️⃣ Offline mode (read from JSON)
If you exported data from Mongo as JSON:
```bash
python process_data.py --input-json videos_dump.json
```

---

## 🧠 Command Summary
| Flag | Description |
|------|--------------|
| `--mongo-uri` | Custom MongoDB URI (overrides .env) |
| `--db` | Database name (auto-detected from URI) |
| `--collection` | Source collection (default: `videos`) |
| `--query` | Mongo filter in JSON string format |
| `--input-json` | Use local JSON file instead of MongoDB |
| `--out-processed` | Output file name for processed videos |
| `--out-summary` | Output file name for dashboard summary |
| `--out-coll-processed` | Mongo collection for processed data |
| `--out-coll-summary` | Mongo collection for summary data |
| `--no-mongo` | Disable auto upsert to Mongo |

---

## 🗂️ Output Collections
After processing, you’ll find two new collections in MongoDB:

- `processed_videos` — per-video time horizon data
- `dashboard_summary` — summarized coverage & milestone status

Both have unique indexes on `video_id` and are safe to re-run (idempotent upsert).

---

## 🧾 Example Console Output
```
✅ Using Mongo URI from .env: mongodb://localhost:27017/ytscan
✅ Auto-detected DB: ytscan
✅ Wrote processed_videos.json (422 rows)
✅ Wrote dashboard_summary.json (422 rows)
⏫ Upserting outputs into Mongo...
   ↳ processed_videos: upserted=180, modified=242
   ↳ dashboard_summary: upserted=180, modified=242
✅ Done upserting to Mongo.
```

---

## 💡 Notes
- Data is always validated to ensure non-decreasing view counts.
- Coverage is computed based on expected timestamps vs actual snapshots.
- Can be safely re-run multiple times without duplicating data.
- Ideal for both data cleaning and real-time dashboard use.

---

📅 **Last updated:** 2025-10-17