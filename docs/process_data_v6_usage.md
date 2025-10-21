# process_data_v6.py — Usage Guide

## Overview
`process_data_v6.py` extends the previous version (v5) with a new **overview summary** feature. It now generates three outputs instead of two, giving you full insight into tracking and processing progress.

**Outputs:**
1. `processed_videos.json` — per-video metrics (views, likes, comments by time horizon)
2. `dashboard_summary.json` — compact dashboard-friendly summary
3. `dashboard_overview.json` — **NEW** global counters for total, processed, and pending videos

---

## 🆕 What's New in v6
- Added support for `--out-dir` and environment variable `OUTPUT_DIR`
- Writes outputs (`processed_videos.json`, `dashboard_summary.json`, `dashboard_overview.json`) to project root or custom directory
- New file: `dashboard_overview.json` with global counts
- Improved skip logic for already processed videos (`read_from_mongo_unprocessed`)
- More verbose console logs with query filters and pipeline info

---

## ⚙️ Requirements
```bash
pip install pymongo python-dotenv
```
Ensure your project contains a `.env` file at the repo root, for example:
```bash
MONGO_URI=mongodb://localhost:27017/ytscan
```

---

## 🧩 Command Options
| Argument | Description |
|-----------|-------------|
| `--mongo-uri` | MongoDB URI (read from `.env` if not provided) |
| `--db` | Database name (auto-detected if omitted) |
| `--collection` | Source collection, default = `videos` |
| `--input-json` | Optional JSON/NDJSON file input |
| `--out-processed` | Filename for processed video output |
| `--out-summary` | Filename for dashboard summary output |
| `--to-mongo` | Force upsert results into MongoDB |
| `--no-mongo` | Disable MongoDB upsert |
| `--query` | MongoDB query filter (default: `{ "tracking.status": "complete" }`) |
| `--out-coll-processed` | MongoDB collection for processed videos |
| `--out-coll-summary` | MongoDB collection for dashboard summary |
| `--skip-processed` | Skip already processed videos (default `true`) |
| `--processed-source-coll` | Override source used for duplicate checking |
| `--out-dir` | Custom output directory (default: project root, or set via `OUTPUT_DIR`) |

---

## 🚀 Example Commands
### 1️⃣ Default Run (MongoDB mode)
```bash
python process_data_v6.py --mongo-uri "mongodb://localhost:27017/ytscan" --db ytscan
```
Produces:
- `processed_videos.json`
- `dashboard_summary.json`
- `dashboard_overview.json`

### 2️⃣ Include All Videos (Ignore skip)
```bash
python process_data_v6.py --mongo-uri "mongodb://localhost:27017/ytscan" --db ytscan --skip-processed=false
```

### 3️⃣ Use Local JSON Dump
```bash
python process_data_v6.py --input-json videos_dump.json
```

### 4️⃣ Push to MongoDB
```bash
python process_data_v6.py --mongo-uri "mongodb://localhost:27017/ytscan" --db ytscan --to-mongo
```

---

## 📊 Output Files

### **1️⃣ processed_videos.json**
Contains one entry per video with full details by time horizon.
```json
{
  "video_id": "abcd1234",
  "status": "complete",
  "published_at": "2025-10-18T04:00:00Z",
  "n_snapshots": 45,
  "last_snapshot_ts": "2025-10-18T10:00:00Z",
  "completed_horizons": [60, 180, 360],
  "n_completed_horizons": 3,
  "horizons": {
    "60": { "views": 1200, "likes": 50, "comments": 3, "value_method": "floor", "coverage_ratio": 0.95 },
    "1440": { "views": null, "value_method": "missing", "coverage_ratio": 0.0 }
  }
}
```
> **Note:** `coverage_ratio` shows how complete the polling data was up to each time horizon (0.0–1.0). Example: `0.80` means 80% of expected snapshots were captured.

### **2️⃣ dashboard_summary.json**
Compact view for analytics tools like Power BI or Grafana.
```json
{
  "video_id": "abcd1234",
  "status": "complete",
  "reached_h1": true,
  "reached_h3": true,
  "reached_h6": true,
  "reached_h12": false,
  "reached_h24": false,
  "coverage_1h": 0.95,
  "coverage_3h": 0.88,
  "coverage_6h": 0.70,
  "n_completed_horizons": 3
}
```

### **3️⃣ dashboard_overview.json (NEW)**
Provides global counts for quick dashboard progress tracking.
```json
{
  "total_videos": 10520,
  "processed_videos": 8120,
  "pending_videos": 2400,
  "timestamp": "2025-10-18T22:31:00Z"
}
```

**Logic:**
- `total_videos` = total count from `videos` collection  
- `processed_videos` = count from `processed_videos` collection  
- `pending_videos` = difference between total and processed  
- If using `--input-json`, only `processed_videos` is filled.

---

## 🔁 Skip-Processed Logic
When `--skip-processed=true` (default):
- Skips any video whose `_id` already exists in `processed_videos.video_id`
- Still filters for `tracking.status == "complete"`

To include all:
```bash
--skip-processed=false
```

---

## ⚠️ Error Handling
- If MongoDB is not reachable, script still saves local JSON outputs.
- Missing `.env` or `--mongo-uri` → prints error and exits (code 2).
- Invalid JSON in `--query` → prints error and exits (code 4).
- Upsert failures are caught safely (files still written).

---

## 📈 Workflow Suggestion
1. Run your YouTube tracker (`discover_once`, `track_once`) continuously.
2. Once per day, execute:
   ```bash
   python process_data_v6.py --mongo-uri "mongodb://localhost:27017/ytscan" --db ytscan
   ```
3. Load `dashboard_overview.json` in Power BI or Streamlit to visualize progress:
   - Processed vs Pending
   - Average coverage ratio
   - Daily video growth

---

## 🧠 Tips
- `n_completed_horizons < 5` means video still in progress.
- Use `dashboard_overview.json` to monitor pipeline performance over time.
- For cron automation (Linux):
  ```bash
  0 * * * * /usr/bin/python3 /path/to/process_data_v6.py --mongo-uri mongodb://localhost:27017/ytscan --db ytscan >> logs/process.log 2>&1
  ```

---

📅 **Last Updated:** **Oct 20 2025**