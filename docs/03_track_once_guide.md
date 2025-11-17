# 03 – `track_once` Worker Guide

This guide explains how to run the **`track_once`** worker for the YouTube AutoScanner project.

It is written for people who download the project for the first time and want a clear, step‑by‑step manual with:

- What `track_once` does
- Required environment variables
- Example configuration (`.env`)
- Example commands on Windows, macOS, and Linux
- How to schedule it to run continuously
- Troubleshooting tips

---

## 1. What `track_once` does

The `track_once` worker is responsible for **updating statistics for videos that are already discovered**.

At a high level it:

1. Connects to MongoDB (same database used by `discover_once`).
2. Selects videos whose `tracking.status` is `"tracking"` and whose `tracking.next_poll_after` is due.
3. Calls the YouTube Data API to fetch **latest stats** (views, likes, comments, etc.).
4. Appends a new entry to the `stats_snapshots` array for each video.
5. Updates `tracking.last_polled_at`, `tracking.poll_count`, and `tracking.next_poll_after`.
6. Optionally stops tracking videos when they reach a configured age or stop condition (for example: age ≥ 24h, removed, private, etc. depending on project config).

You typically run:

- `discover_once` to find **new videos**, then
- `track_once` repeatedly to **keep their stats up to date**.

---

## 2. File location and basic invocation

In the repository structure, the worker normally lives here:

```text
yt-autoscanner/
  worker/
    track_once.py
    discover_once.py
    low_quality_autoflag.py
  config/
  dashboard/
  ...
```

From the **project root**, you can run `track_once` in two main ways:

```bash
# Option 1 – recommended: run as a module
python -m worker.track_once

# Option 2 – direct script call
python worker/track_once.py
```

> 💡 Always run these commands **from the project root**, so that the `worker` and `config` packages can be imported correctly.

---

## 3. Required environment variables

`track_once` reuses the same core environment variables as the other workers (`discover_once`, etc.).

These are the important ones you must configure before running it:

| Variable   | Required | Description                                                                 | Example value                                              |
|-----------|----------|-----------------------------------------------------------------------------|------------------------------------------------------------|
| `MONGO_URI` | Yes    | Connection string for MongoDB, including database name or default database | `mongodb://localhost:27017/ytscan`                        |
| `MONGO_DB`  | No     | Optional explicit database name; overrides the DB name in `MONGO_URI`      | `ytscan`                                                   |
| `YT_API_KEY` | Yes   | YouTube Data API key used to fetch latest statistics                       | `AIza...`                                                  |
| `YT_API_TIMEOUT` | No | Optional HTTP timeout (seconds) for YouTube API calls                     | `30`                                                       |

> ℹ️ The **exact list of optional tuning variables** (batch size, max snapshots, polling intervals, etc.) depends on your project version. In the public setup, these are usually configured inside the worker file or a shared `config` module, not via environment variables.

### 3.1 Example `.env` file

Create a file named `.env` in your project root (same folder that contains the `worker/` folder) with content similar to this:

```env
# ===== MongoDB connection =====
MONGO_URI=mongodb://localhost:27017/ytscan
# Optional – override DB name if not in URI
MONGO_DB=ytscan

# ===== YouTube API key for stats polling =====
YT_API_KEY=YOUR_YOUTUBE_API_KEY_HERE

# Optional extra HTTP timeout (seconds)
YT_API_TIMEOUT=30
```

> 📝 **Note:** Never commit your real API key to a public repo. Add `.env` to `.gitignore`.

Most worker scripts load `.env` automatically via `python-dotenv`, so as long as `.env` is in the project root, you do not need to export each variable manually.

---

## 3.5 Environment Variables (Full List)

Below is the **complete list of environment variables** used by `track_once` in a standard YouTube AutoScanner installation. Some are shared across workers; some are exclusive to the tracking logic.

These variables allow users to configure MongoDB, YouTube API, polling behavior, snapshot limits, stopping conditions, and debugging levels **without modifying code**.

### **3.5.1 Core System Variables**

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `MONGO_URI` | Yes | MongoDB connection string | `mongodb://localhost:27017/ytscan` |
| `MONGO_DB` | No | Override database name | `ytscan` |
| `YT_API_KEY` | Yes | YouTube Data API key | `AIza...` |
| `YT_API_TIMEOUT` | No | HTTP timeout (seconds) | `30` |
| `YT_DEBUG_HTTP` | No | Log HTTP requests and responses | `true` |

---

### **3.5.2 Tracking Logic Variables**

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `TRACK_LIMIT` | No | Max videos per run; overrides CLI `--limit` | `500` |
| `TRACK_FORCE` | No | Force polling even if not due | `false` |
| `TRACK_BATCH_SIZE` | No | Internal fetch batch size per API call | `50` |
| `TRACK_MAX_SNAPSHOTS` | No | Maximum snapshots before auto-stop | `64` |
| `TRACK_STOP_IF_AGE_MIN` | No | Stop videos older than X minutes | `1440` (24h) |
| `TRACK_IGNORE_ERRORS` | No | Continue even when API errors occur | `true` |
| `TRACK_ALLOW_COMPLETE_UPDATE` | No | Allow polling videos marked `complete` | `false` |
| `TRACK_PRINT_PROGRESS` | No | Debug polling progress | `true` |

---

### **3.5.3 Region / Discovery / Routing Variables (Shared)**

These variables are used by both `discover_once` and `track_once` when coordinating polling logic.

| Variable | Description | Example |
|----------|-------------|---------|
| `YT_REGION_CODE` | Restrict operations to a region | `US` |
| `YT_RANDOM_REGION_POOL` | Comma-separated list of allowed regions | `US,CA,GB,AU,JP` |
| `YT_RANDOM_PICK` | Random video routing (1 = on) | `1` |

---

### **3.5.4 Performance & Quota Control Variables**

| Variable | Description | Example |
|----------|-------------|---------|
| `YT_BACKOFF_SECONDS` | Sleep after quota or API errors | `3` |
| `YT_API_RETRIES` | Automatic retry count | `5` |
| `YT_SLOWDOWN_AFTER` | Reduce speed after N requests | `1000` |
| `YT_SLEEP_BETWEEN_CALLS` | Delay between individual API calls | `0.2` |

---

### **3.5.5 Example Extended `.env` File**

Below is the **correct and verified** `.env` example that only includes environment variables **actually supported by the current codebase** (`discover_once.py` + `track_once.py`).

```env
# =========================
# 📌 MongoDB Configuration
# =========================
MONGO_URI=mongodb://localhost:27017/ytscan
MONGO_DB=ytscan

# =========================
# 📌 YouTube API Settings
# =========================
YT_API_KEY=YOUR_API_KEY_HERE
YT_API_TIMEOUT=30
YT_API_RETRIES=5
YT_BACKOFF_SECONDS=3

# =========================
# 📌 discover_once Settings
# =========================
YT_REGION=US
YT_QUERY=news
YT_SINCE_MINUTES=10
YT_MAX_PAGES=3
YT_DURATION_MODE=mix
YT_DURATION_POOL=short:2,medium:2,long:1,any:0
YT_EXCLUDE_LIVE=1

# Random mode
YT_RANDOM_PICK=1
YT_RANDOM_REGION_POOL=US,GB,CA,AU,IN,JP,VN,KR,FR,DE
YT_RANDOM_QUERY_POOL=music:5,news:4,gaming:5,ai:4,travel:3
# Optional region-specific overrides:
# YT_RANDOM_QUERY_POOL_US=breaking news:5,live:4,stock market:3

# =========================
# 📌 track_once Settings
# =========================
TRACK_LIMIT=300
TRACK_MAX_SNAPSHOTS=64
TRACK_STOP_IF_AGE_MIN=1440
TRACK_IGNORE_ERRORS=true
TRACK_PRINT_PROGRESS=true

YT_SLEEP_BETWEEN_CALLS=0.2
YT_SLOWDOWN_AFTER=1000
``` `.env` File**

---

### 3.5.7 Verified env vars used by `discover_once.py`
 Verified env vars used by `discover_once.py`

The environment variables below are **confirmed from the actual source code** (`discover_once.py v4.4`) and your autorun script. These are the **only env vars** that currently affect behavior.

| Env name | Purpose |
|----------|---------|
| `YT_API_KEY` | YouTube Data API key (required). |
| `MONGO_URI` | MongoDB connection URI. |
| `MONGO_DB` | Optional DB override (used only in `log_worker_run`). |
| `YT_REGION` | Default region when random mode is OFF. |
| `YT_QUERY` | Fallback keyword when random selection fails. |
| `YT_RANDOM_PICK` | Enables random region/query mode. |
| `YT_RANDOM_REGION_POOL` | Comma‑separated region codes for random selection. |
| `YT_RANDOM_QUERY_POOL` | Global weighted query pool. |
| `YT_RANDOM_QUERY_POOL_<REGION>` | Region‑specific weighted pools (optional). |
| `YT_SINCE_MINUTES` | How far back (in minutes) to search for new videos. |
| `YT_MAX_PAGES` | Max YouTube search pages per run. |
| `YT_DURATION_MODE` | Duration mode: `any` / `short` / `medium` / `long` / `mix`. |
| `YT_DURATION_POOL` | Weighted buckets for duration mix mode. |
| `YT_EXCLUDE_LIVE` | Whether to ignore live/upcoming streams. |


