All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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