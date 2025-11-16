# YT AutoScanner — MongoDB Collections Overview  

This document outlines the four core MongoDB collections of **YT AutoScanner**, including schema design, field meanings, and how each collection interacts within the system’s data pipeline.

---

# 📁 1. Collection: `videos`

**Purpose:**  
This is the *main* and most important collection. It stores raw metadata pulled from YouTube, plus tracking snapshots, ML flags, and worker statuses.

**Lifecycle:**  
discover_once → videos → track_once → ML scoring → stopped/complete

### High-level Schema
```yaml
_id: string (video_id)
snippet:
  title: string
  publishedAt: ISODate
  thumbnails: { default, medium, high }
  channelId: string
  categoryId: string
  durationISO: string
  durationSec: number
  lengthBucket: short | medium | long

source:
  query: string
  regionCode: string
  randomMode: boolean

stats_snapshots:
  - ts: ISODate
    viewCount: number
    likeCount: number
    commentCount: number

ml_flags:
  viral_v1: { likely, confirmed, score, updated_at } # The team is planning this
  low_quality_v1_3h: { is_low, score, threshold, updated_at } # already run
  low_quality_v3_6h: { is_low, score, threshold, updated_at } # already run

tracking:
  status: tracking | removed | stopped | complete
  discovered_at: ISODate
  last_polled_at: ISODate
  next_poll_after: ISODate
  poll_count: number
  stop_reason: string | null
```

---

# 📁 2. Collection: `processed_videos`

**Purpose:**  
Stores ML-ready, feature-engineered output from `process_data.py`. Used for training machine learning models

### High-level Schema
```yaml
_id: ObjectId
video_id: string

completed_horizons: [60, 180, 360, 720, 1440]
coverage_score: float
growth_phase: flat | rising | volatile

last_snapshot_ts: ISODate
n_snapshots: number
published_at: ISODate

horizons:
  "60": { views, likes, comments, coverage_ratio, ... }
  "180": { ... }
  "360": { ... }
  "720": { ... }
  "1440": { ... }

snapshot_features:
  v_slope_mean: float
  v_slope_max: float
  v_slope_std: float
  v_accel_mean: float
  time_first_1k: number
  time_first_10k: number
  extended:
    "180": { v_slope_mean, v_slope_max, plateau, low_activity, coverage_ratio }
    "360": { ... }
    "720": { ... }
    "1440": { ... }

processed_at: ISODate
processed_status: tracking | complete

ml_flags:
  viral_v1: { likely, confirmed, score }
  low_quality_v1_3h: { is_low, score }
  low_quality_v3_6h: { is_low, score }
```

---

# 📁 3. Collection: `channels`

**Purpose:**  
Provides channel enrichment for filtering, ranking, and additional ML features (for future development)

### High-level Schema
```yaml
_id: string (channelId)

snippet:
  title: string
  handle: string
  publishedAt: ISODate
  country: string | null

stats:
  subscriberCount: number
  videoCount: number
  viewCount: number

analytics:
  recentUploadCount_30d: number
  view_efficiency_mean: float
  view_efficiency_powerlaw: float
  channel_stability_index: float
  channel_activity_score: float
  channel_trust_score: float

derived:
  channelAgeDays: number
  avgViewsPerVideo: float
  uploadFreqPerWeek: float

last_checked_at: ISODate
etag: string
```

---

# 📁 4. Collection: `worker_runs`

**Purpose:**  
Monitoring + operational metrics for each worker (you can view their last run on the dashboard)

### High-level Schema
```yaml
_id: ObjectId
name: discover_once | track_once | low_quality_autoflag
last_run: ISODate
status: ok | error

pages: number
total_found: number
total_upserted: number

processed: number
completed: number

low_3h: number
low_6h: number
scored_3h: number
scored_6h: number
skipped_age_lt_3h: number
skipped_age_lt_6h: number
skipped_feat_3h: number
skipped_feat_6h: number
total_docs: number
total_updated: number
```

---

# 🔄 Data Flow Summary

discover_once  
→ videos  
→ track_once  
→ ML scoring  
→ videos (stopped/complete)  
→ processed_videos  
→ ML training

---

# ✔️ Final Notes
- `videos` = source of truth  
- `processed_videos` = ML feature dataset  
- `channels` = enrichment  
- `worker_runs` = monitoring layer  

📅 **Last Updated:** **Nov 15 2025**
