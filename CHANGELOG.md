All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
## [Oct 26 2025]
### 🚀 Improved / Updated
- Data footprint reduction across ingestion pipeline:
  - Removed **`channelTitle`** and **`channelHandle`** from `discover_once` output
  - Lightweight insert-only design maintained ✅
- Introduced **`prune_unavailable_once.py`**:
  - Hard-deletes **unavailable / no_publishedAt** videos instantly
  - Optional API re-check for stronger validation
  - Ensures data quality for early engagement modeling
- Enhanced **`archive_completed_videos.py`**:
  - 🔹 Partitioned cold collections → `videos_cold_YYYY_MM`
  - 🔹 Trimming heavy fields (`thumbnails`, long description)
  - Batch-safe UPSERT → delete workflow
  - Keeps `videos` hot store **lean and performant**
- Documentation improvements:
  - Added **English technical docs** for 4 core tools:
    - `discover_once.md`
    - `track_once.md`
    - `archive_completed_videos.md`
    - `prune_unavailable_once.md`
  - Clear operational roles and lifecycle transitions
- Tracking lifecycle clarity:
  - Updated behavior explanation for `track_once`
  - Stop conditions defined for ML training integrity

### ✅ Results
- Hot dataset shrinks automatically
- Cleaner ingestion → faster tracking
- Long-term data still retained in cold storage
- Team onboarding & maintenance improved significantly

> These improvements strengthen the **data lifecycle**, reduce costs, and set a strong foundation for **virality prediction accuracy**.

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

📅 **Last Updated:** **Oct 21 2025**