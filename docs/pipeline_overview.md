# Pipeline Overview (Local-Only)

> **Scope:** This document explains the end-to-end data pipeline for the YouTube Tracker project in **local** mode (no VPS/deploy). It also specifies the **current JSON file structures** produced by the processing step.

---

## 1) High-Level Flow

```
[YouTube Data API]
       │
       ▼
discover_once.py        →  Insert new video metadata
       │
       ▼
track_once.py           →  Append time-based stats snapshots
       │
       ▼
MongoDB (db: ytscan, coll: videos)
       │
       ▼
process_data.py         →  Clean + summarize + export JSON
       │                    ↳ processed_videos.json
       │                    ↳ dashboard_summary.json
       └──────────────────→  (auto upsert to Mongo: processed_videos, dashboard_summary)
```

**Stage roles**
- **discover_once.py**: Finds fresh videos (keyword/region/random) and writes base metadata into `ytscan.videos`.
- **track_once.py**: Periodically polls statistics (views/likes/comments) for each tracked video and appends to `stats_snapshots` array.
- **process_data.py** (local):
  - Reads from `ytscan.videos` (or a local JSON dump)
  - Cleans/enforces monotonic views
  - Computes horizon values at **1h, 3h, 6h, 12h, 24h** using **floor → ceil(+30m) → missing** rule
  - Computes **coverage_ratio** per horizon based on sampling plan (5’/15’/30’/60’)
  - **Exports JSON files** (local): `processed_videos.json`, `dashboard_summary.json`
  - **By default**, also upserts those results into Mongo collections with the same names

---

## 2) Temporal Plan (Sampling Density)

To capture early growth with higher temporal resolution while conserving API quota later, the sampling frequency is gradually reduced over the 24-hour tracking period as follows:

| Time Range | Interval | Duration (minutes) | Samples |
|-------------|-----------|--------------------|----------|
| **0–2 h**   | Every **5 min**  | 120 | 120 ÷ 5 = **24** |
| **2–6 h**   | Every **15 min** | 240 | 240 ÷ 15 = **16** |
| **6–12 h**  | Every **30 min** | 360 | 360 ÷ 30 = **12** |
| **12–24 h** | Every **60 min** | 720 | 720 ÷ 60 = **12** |

**Total samples:**  
`24 + 16 + 12 + 12 = 64 timestamps`  
*(≈ 65 if including both the start and end points)*

> ✅ This temporal plan provides fine-grained sampling in the early stage (first 2 hours) and reduces frequency later, balancing data resolution and quota efficiency across the full 24-hour observation window.

---

## 3) Source Document (Mongo: ytscan.videos)

A single video document in `videos` typically looks like:

```json
{
  "_id": "VIDEO_ID",
  "source": {
    "query": "search terms",
    "regionCode": "US",
    "randomMode": false
  },
  "snippet": {
    "title": "Video title",
    "publishedAt": "2025-10-17T03:25:00Z",
    "thumbnails": { "...": "..." },
    "channelId": "CHANNEL_ID",
    "channelTitle": "Channel name",
    "categoryId": "22"
  },
  "tracking": {
    "status": "tracking | complete | error",
    "discovered_at": "2025-10-17T03:25:00Z",
    "last_polled_at": "2025-10-17T05:15:00Z",
    "next_poll_after": "2025-10-17T05:30:00Z",
    "poll_count": 10,
    "stop_reason": null
  },
  "stats_snapshots": [
    {
      "ts": "2025-10-17T03:30:00Z",
      "viewCount": 1200,
      "likeCount": 70,
      "commentCount": 4
    }
    // ... ≈64 entries in 24h (≈65 if including t=0)
  ]
}
```

> Notes
> - `viewCount` is treated as **non-decreasing** during processing.
> - `likeCount`/`commentCount` may be missing or partially available depending on API responses.

---

## 4) Output JSON Files (Local)

### 4.1 processed_videos.json
**Granularity:** 1 row per **video** (detailed horizons).  
**Usage:** ML features, deeper analysis.

**Example entry:**
```json
{
  "video_id": "VIDEO_ID",
  "status": "complete",
  "published_at": "2025-10-17T03:25:00Z",
  "n_snapshots": 78,
  "last_snapshot_ts": "2025-10-17T23:20:00Z",
  "horizons": {
    "60":   { "views": 1200, "likes": 70,  "comments": 4,  "value_method": "floor", "coverage_ratio": 0.62, "n_expected": 12, "n_available": 8 },
    "180":  { "views": 4200, "likes": 190, "comments": 21, "value_method": "floor", "coverage_ratio": 0.60, "n_expected": 28, "n_available": 17 },
    "360":  { "views": 9200, "likes": 440, "comments": 48, "value_method": "ceil",  "coverage_ratio": 0.51, "n_expected": 44, "n_available": 22 },
    "720":   { "views": 22000,"likes": 900, "comments": 120,"value_method": "floor", "coverage_ratio": 0.80, "n_expected": 52, "n_available": 42 },
    "1440": { "views": 56000,"likes": 2300,"comments": 380,"value_method": "floor", "coverage_ratio": 0.92, "n_expected": 64, "n_available": 59 }
  }
}
```

**Field meanings:**
- `horizons["60"|"180"|"360"|"1440"]` correspond to **1h, 3h, 6h, 24h**.
- `value_method`:
  - `floor`: last snapshot ≤ horizon
  - `ceil`: first snapshot after horizon within +30 minutes
  - `missing`: neither available (insufficient coverage)
- `coverage_ratio` = observed snapshots up to horizon / expected snapshots up to horizon (per sampling plan).

---

### 4.2 dashboard_summary.json
**Granularity:** 1 row per **video** (lightweight).  
**Usage:** Fast dashboards, quick monitoring.

**Example entry:**
```json
{
  "video_id": "VIDEO_ID",
  "status": "complete",
  "published_at": "2025-10-17T03:25:00Z",
  "n_snapshots": 78,
  "last_snapshot_ts": "2025-10-17T23:20:00Z",
  "reached_h1":  true,
  "reached_h3":  true,
  "reached_h6":  true,
  "reached_h12": true,
  "reached_h24": true,
  "coverage_1h": 0.62,
  "coverage_3h": 0.60,
  "coverage_6h": 0.51,
  "coverage_12h": 0.80,
  "coverage_24h": 0.92
}
```

**Logic:**
- `reached_h*` is **true** if `value_method ∈ {"floor","ceil"}` for the horizon.
- Coverage values mirror `processed_videos.json` but are flattened for quick visuals.

---

## 5) Local Execution Notes

- Ensure `.env` exists in the project root:
  ```env
  YT_API_KEY=YOUR_YOUTUBE_API_KEY
  MONGO_URI=mongodb://localhost:27017/ytscan
  ```
- Run the processor (default auto-upsert to Mongo):
  ```bash
  python .worker\process_data.py
  ```
- Optional flags:
  ```bash
  # JSON only (skip Mongo upsert)
  python .worker\process_data.py --no-mongo

  # Filter a subset of videos
  python .worker\process_data.py --query '{"tracking.status":"complete"}'
  ```

**Generated files (local):**
- `processed_videos.json`
- `dashboard_summary.json`

> Tip: Add to `.gitignore` if you don’t want these tracked by Git:
> ```
> processed_videos.json
> dashboard_summary.json
> ```

---

## 6) Future Extensions (Optional)
- Add hourly job to trigger `process_data.py` automatically.
- Store Parquet versions for ML training efficiency.
- Enrich outputs with derived rates (growth per interval, like/view ratio) when coverage is sufficient.
- Add region/channel/category dimensions to summaries for richer dashboards.

---

**Last updated:** 2025-10-17
