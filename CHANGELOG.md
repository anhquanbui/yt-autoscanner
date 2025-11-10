All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---
## [Nov 10 2025]

### ✨ **New Features**
🧭 **Centralized Export Path (`config/path_utils.py`)**
  - Introduced centralized utility to manage all export/output directories.
  - Supports `.env` variables: `EXPORT_DIR` and `OUTPUT_DIR`.
  - Automatically creates export directories if missing, ensuring cross-platform compatibility.
  - Simplifies path management for `process_data.py` and all future tools.

🧾 **Mongo → Parquet Export Tool (`tools/mongo_to_parquet.py`)**
  - New command-line tool to export MongoDB collections to ML-ready **Parquet** files.
  - Handles large datasets safely using **chunked writes** (`--chunk` flag).
  - Supports filters (`--query`), limits (`--limit`), and custom output file names (`--out`).
  - Auto-converts BSON types (`ObjectId`, `Decimal128`, etc.) to compatible formats.
  - Displays progress bar with `tqdm`.

🧮 **Process Data Integration (`worker/process_data.py`)**
  - Integrated with centralized export system using `get_export_dir()`.
  - Unified all output paths (JSON, CSV, Parquet) under a single location.
  - Cleaner structure and easier ML hand-off.

---

### ⚙️ **Configuration**
- Added new environment variable in `.env`:
  ```env
  EXPORT_DIR=D:\PYTHON\PROJECT\yt-autoscanner\data_export
  ```
  Default fallback (if unset):  
  ```
  <project_root>/data_export/
  ```

---

### 🧠 **Development Environment**
- Added new dependencies to `requirements-dev.txt`:
  ```bash
  pyarrow>=17.0.0
  fastparquet>=2024.5.0
  odfpy>=1.4.1
  tqdm>=4.66.5
  ```
- Updated comments and version consistency for ML-ready export utilities.

---

### 🗂️ **Structural Cleanup**
- Refactored `/tools/` directory:
  ```
  backfill_channels.py
  backfill_missing_fields.py
  make_indexes.py
  mongo_to_parquet.py
  ```
  ➤ Removed all legacy `.bak` files and unused scripts.  
  ➤ Added unified output folder:
  ```
  /yt-autoscanner
  /data_export
  ```

---
## [Oct 26 2025] — Major Data Lifecycle Upgrade 🚀

### 🔥 Data Footprint Optimization
- Removed **`channelTitle`** & **`channelHandle`** from `discover_once.py`
- Video ingestion remains **lightweight insert-only**
- Reduced document size in hot store (`videos`)

→ Faster insert, lower storage growth, higher throughput.

---

### 🧱 New Tool: `prune_unavailable_once.py`
- Hard-delete videos that are:
  - `unavailable`, `private`, `no_publishedAt`
- Optional YouTube API confirmation mode
- Keeps only high-quality inputs for ML

✅ Removes true noise from dataset

---

### 🧊 Cold Storage Layer for Complete Videos
**Enhanced `archive_completed_videos.py`:**
- Moves fully tracked videos to:
  ```
  videos_cold_YYYY_MM
  ```
- Removes **heavy snapshot metadata**
- Safe archive → delete workflow

✅ Hot dataset shrinks automatically → faster queries

---

### 🧠 Backfill Channels v2 — Full Refresh Rework
- Aggregation-based picking (no full scans)
- UC-only ID filtering (stable API response)
- Auto-load `.env` for credentials
- New flags:
  - `--limit 0` → unlimited processing
  - `--loop-until-empty` → batch automation
- Derived ML channel metrics:
  - `channelAgeDays`
  - `avgViewsPerVideo`
  - `uploadFreqPerWeek`

✅ Designed for scaling past 100K+ channels

---

### 🎯 Improved Tracking Lifecycle Stability
- Clearer stop conditions in `track_once`
- Only update when **data changed**
- Prevent useless polling after 24h

✅ Better consistency for early-signal modeling

---

### 📄 Documentation Expansion
Added new **English** docs:
- `discover_once.md`
- `track_once.md`
- `archive_completed_videos.md`
- `prune_unavailable_once.md`
- `backfill_channels.md`

Clear lifecycle:
**DISCOVER → TRACK → ARCHIVE → PRUNE → BACKFILL**

---

---
## [Oct 24 2025]
### 🚀 Improved / Updated
- Upgraded **`make_indexes.py`** with full smart indexing workflow (version 3)
- All indexes now follow **real workload optimization**:
  - `_id` uses **YouTube videoId** → not need index `video_id`
  - Query-heavy workload **compound-optimized**:  
    `Equality → Sort → Range`
- Performance tuning:
  - Added **compound indexes** cho `videos`:
    - `channelId + publishedAt` (fetch latest by channel)
    - `regionCode + publishedAt` (region reporting)
    - `categoryId + lengthBucket + publishedAt` (analytics)
  - Added **partial index** (`tracking.status ∈ ["queued","tracking","retry"]`)  
    → reduce IO, queue lookup quicker
- Compatibility enhancement:
  - Updated fields to match current schema:  
    `last_snapshot_ts`, `last_updated`, `_id = videoId`
- Index safety improvements:
  - Avoids recreating indexes when the keys match even if the name or options differ.
  - Optional **selective index cleanup** (accurate drop-old with signature check)

### 🧰 Developer Experience
- Automatic **index name assignment** for readability
- Enhanced logging metadata (`index_maintenance.log`)
- Strict index map ensures **schema-aware indexing**
- Codebase refactor → future use easily

### ✅ Supported Collections
- `videos`: tracking, time-sort, compound analytical indexes
- `processed`: unique per video + analytical last_snapshot
- `channels`: lookup by handle + freshness tracking

---

## [Oct 22 2025]
### 🚀 Added
- Introduced **`make_indexes_v2.py`** with **multi-collection support** (`videos`, `channels`, `processed`)
- Added CLI flags:
  - `--show-only` — preview actions without writing to DB  
  - `--drop-old` — safely remove indexes not defined in the standard map  
  - `--collections` — restrict operations to specific collections
- Implemented **duplicate index detection** (skips existing indexes automatically)
- Implemented **background index creation** (non-blocking operations)
- Added **logging system** (console + file `index_maintenance.log`)
- Added **index cleanup mechanism** for unused or outdated indexes
- New **index map definitions** for:
  - `videos`: tracking, publishedAt, region, channelId, lengthBucket, categoryId  
  - `channels`: handle, last_checked_at  
  - `processed`: video_id, status, snapshot_time

### 🧠 Improved
- Better modular code structure with helper functions:
  - `create_or_verify_indexes()`
  - `drop_unused_indexes()`
- Human-readable console output (with emojis and status indicators)
- Safe re-runs (idempotent design — can be executed multiple times)

### 🧾 Documentation
- Added `make_indexes_v2.md` — full technical documentation and CLI guide.
- Added `CHANGELOG.md` (this file) for version tracking and maintenance logs.

---

## [Oct 21 2025]
### Added
- **`tools/process_data.py v7`** — Major upgrade of the data processing pipeline:
  - Introduced new analytical fields: **`source_meta`**, **`coverage_score`**, **`snapshot_features`**, **`growth_phase`**, and **`ml_flags`** to support advanced ML model training and analytics..
  - Default query now automatically includes both **`complete`** and **`tracking`** videos for near-real-time analysis.
  - Added **`--refresh-existing`** CLI flag to fully **replace documents** in MongoDB (using `ReplaceOne`) instead of updating via `$set`.
  - Added automatic generation of **`dashboard_overview.json`** summarizing total, processed, and pending videos.
  - Enhanced output directory handling via `--out-dir` or environment variable `OUTPUT_DIR`.
  - Improved console logging to display normalized MongoDB query filters.

### Changed
- Normalized query handling in `main()` to prevent overwriting defaults when `--skip-processed=false`.
- Unified default query logic across `read_from_mongo` and `read_from_mongo_unprocessed` to ensure consistency.
- Optimized code structure and modularized snapshot feature computations.
- Updated `process_data.py` to include `tracking` videos without requiring a manual collection drop.

### Fixed
- Resolved missing argument error for `--refresh-existing` in argparse.
- Fixed issue where `tracking` videos were ignored unless an explicit `--query` was provided.
- Improved coverage and snapshot computation for videos with irregular or sparse data.

## [Oct 20 2025]
### Added
- **`worker/discover_once.py v4.3`** — Added automatic filtering to **exclude live and upcoming videos** from discovery results.
- **`worker/track_once.py v3.1`** — Enhanced duration backfill logic for videos missing `durationISO` or `lengthBucket`.
- **`tools/backfill_missing_fields.py` (new)** — New standalone script to backfill missing metadata (duration, handles, etc.) without affecting tracker performance.
- **`.gitignore`** — Updated to exclude `.bak` files.
- **`README.md`** — Updated with new sections for discovery filters, duration backfill, and backfill tool description.

### Changed
- Improved logging and console output in PowerShell runner to display duration mode and skip conditions.
- Reorganized documentation to show most recent updates first and linked full changelog.

### Fixed
- Indentation bug in older discover worker functions.
- Occasional path conflict on MongoDB `$setOnInsert` for snippets.

---

## [Oct 17 2025]
### Added
- **`tools/process_data.py` v1.0** — New CLI script for processing local JSON data and pushing results to MongoDB.
- **`worker/discover_once.py v4.2`** — Refactored for lightweight near-now scan (no lookback > 24h). Added duration enrichment.
- **`worker/track_once.py v3.0`** — Tracks video stats across milestone intervals (5m–60m), marks videos complete after 24h.
- **`tools/backfill_channels_v2.py`** — Adds channel metadata and stats with auto-detection of stale documents.
- **`run_both_local.ps1` v5** — Unified runner for discovery + tracker; supports real-time logging and safe quota stop.

### Changed
- Unified logging across discover/track scripts.
- Updated documentation and file structure in README.
- Added random region/query weighting system for discovery.

📅 **Last Updated:** **Oct 26 2025**