# 📚 Backfill Channels — v2 (detailed)

**File:** `tools/backfill_channels.py`

Refresh and enrich **YouTube channel** metadata in MongoDB using `videos` as the source of truth. Quota‑aware, aggregation‑based picking, and lightweight **derived ML features**.

---

## 🎯 What this tool does

* Finds **channelIds** to refresh (from `videos`, or optionally from `channels`).
* Calls YouTube API `channels.list?part=snippet,statistics` with minimal `fields`.
* Upserts into `channels` collection:

  * `snippet.{title, handle(@customUrl), publishedAt, country}`
  * `stats.{subscriberCount, videoCount, viewCount}`
  * `derived.{channelAgeDays, avgViewsPerVideo, uploadFreqPerWeek}`
  * `etag`, `last_checked_at`

Designed for: **daily refresh**, **initial seeding**, and **full DB cleanup**.

---

## 🧩 Collections used

| Collection | Purpose                                                                                         |
| ---------- | ----------------------------------------------------------------------------------------------- |
| `videos`   | Source of channelIds (via `snippet.channelId`) and optional time window (`snippet.publishedAt`) |
| `channels` | Destination for channel profile + stats + derived features                                      |

> Picking is done with MongoDB **aggregation** (`$group + $lookup + $match`). No Python full scans.

---

## 🔐 Prerequisites

1. `.env` at repo root (auto‑loaded):

```
YT_API_KEY=YOUR_API_KEY
MONGO_URI=mongodb://localhost:27017/ytscan
```

2. Python deps: `requests`, `pymongo`, `python-dotenv`
3. Recommended indexes:

```js
db.channels.createIndex({ last_checked_at: 1 }, { name: "last_checked_at" })
db.videos.createIndex({ "snippet.channelId": 1, "snippet.publishedAt": -1 }, { name: "chan_pub" })
```

---

## ⚙️ CLI options

```
python tools/backfill_channels.py [options]

--videos-since-hours N   Only consider channels from videos newer than N hours (default: 72). Use 0 = ALL videos.
--stale-hours N          Refresh only if last_checked_at older than N hours (default: 48). Use 0 = ignore staleness.
--limit N                Max channels to process this run (default: 2000). Use 0 = unlimited (no $limit in pipeline).
--scan-all-channels      Pick from the entire `channels` collection instead of discovering via `videos`.
--loop-until-empty       Keep looping until no more channels picked/changed.
--dry-run                Do not write to DB; print summary only.
--verbose                Extra logs and diagnostics.
```

---

## ✅ Common recipes

### 1) Initial seed from ALL videos (fastest way)

```powershell
python tools/backfill_channels.py --videos-since-hours 0 --stale-hours 0 --limit 0 --loop-until-empty --verbose
```

> Sweeps every channelId that ever appeared in `videos`. Loops until clean.

### 2) Daily smart refresh (quota‑friendly default)

```powershell
python tools/backfill_channels.py --videos-since-hours 72 --stale-hours 48 --limit 5000
```

> Refresh channels tied to videos in last 72h, but only if stale ≥ 48h.

### 3) Full scan of existing channels (rare)

```powershell
python tools/backfill_channels.py --scan-all-channels --stale-hours 168 --limit 10000
```

> Weekly/monthly hygiene for long‑lived repos.

### 4) Dry‑run first (safe preview)

```powershell
python tools/backfill_channels.py --videos-since-hours 72 --stale-hours 48 --limit 1000 --dry-run --verbose
```

### 5) Re‑touch only truly stale channels (weekly)

```powershell
python tools/backfill_channels.py --videos-since-hours 720 --stale-hours 168 --limit 10000
```

### 6) Bash equivalents (Linux/macOS)

```bash
python tools/backfill_channels.py --videos-since-hours 0 --stale-hours 0 --limit 0 --loop-until-empty --verbose
python tools/backfill_channels.py --videos-since-hours 72 --stale-hours 48 --limit 5000
```

---

## 🧠 Derived features (computed offline)

* `channelAgeDays` = days since `snippet.publishedAt` (channel creation).
* `avgViewsPerVideo` = `viewCount / max(videoCount,1)`.
* `uploadFreqPerWeek` ≈ `videoCount / (channelAgeDays/7)`.

These improve ML quality without extra API calls.

---

## 🧪 Output example

```
>>> backfill_channels | scan_all=False | videos_since_hours=72 | stale_hours=48 | limit=2000 | dry_run=False
[pick] from videos: 412 channels (sample=['UC...', 'UC...'])
DONE. picked=412 fetched=400 changed=395
```

* **picked**: channels selected by the aggregation
* **fetched**: channels returned by YouTube API
* **changed**: upserted/modified documents in `channels`

> Extra diagnostics: if a batch returns 0 items, tool prints `[api] batch returned 0 items. sample ids=[...]`.

---

## 🔍 Troubleshooting

**Q: `picked>0` nhưng `fetched=0`?**
A: Check API key / quota / ID format:

* Ensure `$env:YT_API_KEY` (Windows) or `echo $YT_API_KEY` (bash) is set.
* `.env` must exist at the **working directory** where you run the script.
* Sample test:

  ```powershell
  curl "https://www.googleapis.com/youtube/v3/channels?part=statistics&id=UC_x5XG1OV2P6uZZ5FSM9Ttw&key=$env:YT_API_KEY"
  ```
* Tool auto‑logs a few sample IDs when a batch returns empty.

**Q: `nothing to do` but I expect work?**

* Maybe your window is too strict. Try: `--videos-since-hours 0 --stale-hours 0`.
* If using `--scan-all-channels`, collection may be fresh; set `--stale-hours 0`.

**Q: Want unlimited batch in one run?**

* Use `--limit 0` (disables `$limit` in pipeline). For safety, prefer `--loop-until-empty`.

**Q: Invalid channel IDs?**

* The picker filters to IDs starting with `UC`. If your data contains non‑UC IDs, they are skipped.

**Q: .env not loading?**

* Run the script from the repo root (where `.env` lives) or set env vars explicitly in the shell.

---

## 🚦 Exit codes

| Code | Meaning                                                               |
| ---- | --------------------------------------------------------------------- |
| `0`  | Success                                                               |
| `2`  | Missing `YT_API_KEY`                                                  |
| `88` | Quota exceeded (`quotaExceeded/dailyLimitExceeded/rateLimitExceeded`) |
| else | Other runtime error                                                   |

---

## 🗃️ Data model written to `channels`

```json
{
  "_id": "UC123...",
  "snippet": {
    "title": "Channel Name",
    "handle": "@channel",
    "publishedAt": "2016-06-01T00:00:00Z",
    "country": "US"
  },
  "stats": {
    "subscriberCount": 480000,
    "videoCount": 312,
    "viewCount": 51000000
  },
  "derived": {
    "channelAgeDays": 3500,
    "avgViewsPerVideo": 163461.5,
    "uploadFreqPerWeek": 0.624
  },
  "etag": "abcd1234-xyz",
  "last_checked_at": "2025-10-26T07:30:45Z"
}
```

---

## 🧭 Scheduling suggestions

* **Seed job (one‑time):** run with `--videos-since-hours 0 --stale-hours 0 --limit 0 --loop-until-empty`.
* **HOT queue (active channels):** every 24–48h with `--videos-since-hours 72 --stale-hours 48`.
* **WARM queue:** weekly with `--videos-since-hours 720 --stale-hours 168`.
* **COLD hygiene:** monthly with `--scan-all-channels --stale-hours 720`.

---

## 🧪 VS Code Tasks (optional snippet)

`.vscode/tasks.json` example:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Backfill: Seed All",
      "type": "shell",
      "command": "python tools/backfill_channels.py --videos-since-hours 0 --stale-hours 0 --limit 0 --loop-until-empty --verbose",
      "options": { "cwd": "${workspaceFolder}" }
    },
    {
      "label": "Backfill: Daily Smart",
      "type": "shell",
      "command": "python tools/backfill_channels.py --videos-since-hours 72 --stale-hours 48 --limit 5000",
      "options": { "cwd": "${workspaceFolder}" }
    }
  ]
}
```

---

## 📝 Changelog

* **Oct 26, 2025 — v2**: aggregation picking, derived features, `.env` support, `--limit 0`, `--loop-until-empty`, UC‑only filtering, improved diagnostics.

> Questions or improvements? Open an issue or ping the team. This tool is designed to scale from **tens of thousands** to **millions** of videos while keeping API usage efficient.

---

📅 **Last Updated:** **Oct 26 2025**