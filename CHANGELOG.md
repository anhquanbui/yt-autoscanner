All notable changes to this project will be documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

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