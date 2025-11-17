# 03 — `discover_once` Guide

A clean and structured guide explaining how `discover_once` works, how it discovers near‑now YouTube uploads, how it enriches metadata, and how it inserts standardized documents into MongoDB. Includes tables, examples, and a complete sample `.env` template.

---

## ⭐ Overview
`discover_once` scans **recently uploaded YouTube videos** within a defined timeframe, enriches them with metadata (duration, category, length bucket), and initializes documents in the `videos` collection. It is used both for testing (manual CLI) and continuous discovery (autorun scripts).

---

## ⚙️ Processing Flow (Step-by-Step)

### 1. Load configuration
Reads:
- YouTube API key
- MongoDB connection
- Region/query random pools
- Time window settings

### 2. Pick region & query
- If `YT_RANDOM_PICK=1` → pick random region + weighted query.
- Otherwise uses `YT_REGION` + `YT_QUERY`.

### 3. Define near-now window
Compute:
```
publishedAfter = now - YT_SINCE_MINUTES
```

### 4. Call YouTube Search API
- Uses: `order=date`, `type=video`, region, search query
- Supports optional duration filter
- Fetches up to `YT_MAX_PAGES`

### 5. Optional livestream filtering
- If `YT_EXCLUDE_LIVE=1`, remove videos where `liveBroadcastContent != 'none'`.

### 6. Enrich via `videos.list`
- Fetch duration + category ID
- Convert ISO → seconds
- Classify bucket: `short` / `medium` / `long`

### 7. Upsert into MongoDB
- Creates full standardized document structure
- Updates snippet fields if document already exists
- Initializes tracking + ml_flags

### 8. Log run result
Records status into `worker_runs`.

---

## 📦 MongoDB Document Structure

```json
{
  "_id": "<videoId>",
  "source": {
    "query": "<query>",
    "regionCode": "US",
    "randomMode": true
  },
  "snippet": {
    "title": "...",
    "publishedAt": "2025-11-17T11:00:00Z",
    "thumbnails": {},
    "channelId": "...",
    "categoryId": "24",
    "durationISO": "PT9M30S",
    "durationSec": 570,
    "lengthBucket": "medium"
  },
  "tracking": {
    "status": "tracking",
    "discovered_at": "<ISO>",
    "last_polled_at": null,
    "next_poll_after": "<ISO>",
    "poll_count": 0,
    "stop_reason": null
  },
  "stats_snapshots": [],
  "ml_flags": {
    "viral_v1": {"likely": false, "confirmed": false, "score": 0.0, "updated_at": null},
    "low_quality_v1_3h": {"is_low": false, "score": 0.0, "threshold": null, "updated_at": null},
    "low_quality_v3_6h": {"is_low": false, "score": 0.0, "threshold": null, "updated_at": null}
  }
}
```

---

## 🧩 Environment Variables (Reference)

### Core YouTube & Mongo
| Variable | Required | Default | Description |
|---------|----------|---------|-------------|
| `YT_API_KEY` | ✔ | — | YouTube API key |
| `MONGO_URI` | ✔ | — | MongoDB connection string |
| `MONGO_DB` | ✔ | yt_autoscanner | Target database |

### Discovery window
| Variable | Default | Meaning |
|----------|---------|---------|
| `YT_SINCE_MINUTES` | 10 | Only fetch videos newer than X minutes |
| `YT_MAX_PAGES` | 5 | Limit number of API search pages |

### Random region & query
| Variable | Default | Meaning |
|----------|---------|---------|
| `YT_RANDOM_PICK` | 1 | Enable automatic random selection |
| `YT_RANDOM_REGION_POOL` | Long CSV | Random region list |
| `YT_RANDOM_QUERY_POOL` | Weighted list | `query:weight` format |

Example weighted list:
```
ai:6, breaking news:5, iphone:5, gaming:6, kpop:5, trailer:5
```

---

## 🖥️ CLI Usage Examples

### Run once
```
python -m worker.discover_once
```

### Force region
```
YT_RANDOM_PICK=0 YT_REGION=JP python -m worker.discover_once
```

### Force query
```
YT_RANDOM_PICK=0 YT_QUERY="breaking news" python -m worker.discover_once
```

### Test extremely fresh uploads
```
YT_SINCE_MINUTES=1 python -m worker.discover_once
```

---

## 🗂️ Example `.env` Template
```
# ==== Mongo ====
MONGO_URI=mongodb://localhost:27017
MONGO_DB=yt_autoscanner

# ==== YouTube API ====
YT_API_KEY=YOUR_KEY

# ==== Discovery settings ====
YT_SINCE_MINUTES=10
YT_MAX_PAGES=5
YT_VIDEO_DURATION=any

# ==== Random discover ====
YT_RANDOM_PICK=1
YT_RANDOM_REGION_POOL=US,GB,CA,AU,IN,JP,VN,KR,FR,DE,BR,MX,ID,TH,ES,IT,NL,SG,MY,PH,TW,HK,AR,CL,TR,PL,SA,AE,EG,NG,KE,RU,SE,NO,FI,DK,IE,PT,GR,IL,ZA

YT_RANDOM_QUERY_POOL= \
  live:6, breaking news:5, world news:5, update:3, politics:3, economy:3, \
  ai:6, artificial intelligence:4, chatgpt:5, openai:4, machine learning:3, \
  tech review:5, iphone:5, samsung:4, camera:4, unboxing:5, \
  gaming:6, esports:5, minecraft:5, valorant:5, league of legends:5
```

---

📅 **Last Updated:** **Nov 17 2025**