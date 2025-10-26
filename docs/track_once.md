# 📈 Track Once — Early Engagement Snapshot Tool  
**File:** `tools/track_once.py`

## 🎯 Purpose  
Collect **progressive YouTube statistics** (views/likes/comments…) within the **first 24 hours** after publication — the most important signals for virality prediction.

This tool:
- Polls the YouTube API for metrics updates
- Builds **time-series signals** (`statistics_snapshots`)
- Detects tracking completion or failures
- Hands off each video to either:
  - **Archiver** → if successfully completed
  - **Pruner** → if unavailable / bad data

---

## 📊 Data Fields Written by Track Once

| Field Group | Details |
|------------|---------|
| Snapshots | `statistics_snapshots[]`, each containing timestamp + stats |
| Tracking | status, stop_reason, poll_count, scheduled polling |
| Timestamps | last_polled_at, next_poll_after |
| Milestones | first_1k_views, first_10k_views, etc. *(configurable)* |

Example snapshot entry:
```json
{
  "at": "2025-10-26T02:04:30.829Z",
  "views": 1254,
  "likes": 82,
  "comments": 9
}
```

---

## 🔁 Polling Behavior

### Every iteration:
1. Fetch batch of active videos  
2. Poll stats (`videos?part=statistics`)  
3. Append new snapshot if stats returned  
4. Schedule next polling time  
5. Detect stop condition if reached

---

## 🛑 Stop Conditions

| Condition | Status | When it happens |
|----------|--------|----------------|
| Finish 24h window ✅ | `complete` | Age > `TRACK_MAX_POLL_HOURS` |
| No stats returned | `unavailable` | Video private/deleted/blocked |
| No publishedAt | `no_publishedAt` | Bad data or API issue |
| Polling logic decides end | `complete` | Enough signals collected |
| Fatal API error | `error` | Optional recovery action |

After stop:
- Archiver may move video → cold storage
- Pruner may delete unusable videos

---

## ⚙️ Poll Scheduling Logic

| Video age | Poll interval | Reason |
|----------|----------------|-------|
| 0–2 hours | ~10–30 sec | Fast growth zone |
| 2–12 hours | ~1–5 min | Slower growth |
| 12–24 hours | ~10–30 min | Tail phase |
| >24 hours | stop | Data not needed for model |

These values are configurable in code or via env.

---

## 📦 Document Lifecycle

```text
Discover Once
   ↓ inserts (status="tracking")
[ videos ]
   ↓ Track Once polling snapshots (until 24h window)
   ├─> status="complete" → Archive
   └─> status="unavailable"/"no_publishedAt" → Prune
```

---

## 🔐 API Safety and Quota Handling

| Feature | Benefit |
|--------|---------|
| Graceful slowdown when quota nearly exceeded | Avoids suspension |
| Bulk API requests | Higher efficiency |
| Retry strategy if temporary API error | Robust ingestion |
| Skip polling for recently-failed videos | Faster turnaround |

Example quota alert behaviors:
- Log warning
- Increase `next_poll_after`
- Break current polling cycle

---

## 🧩 Indexing Requirements

Recommended indexes on `videos`:

```js
db.videos.createIndex({ "tracking.status": 1, "tracking.next_poll_after": 1 })
db.videos.createIndex({ "snippet.publishedAt": 1 })
```

Minimum fields required from discover:
- `_id`
- `snippet.publishedAt`
- `snippet.channelId`

---

## 🔧 Environment Variables

| Env Name | Default | Meaning |
|---------|---------|---------|
| `TRACK_BATCH_SIZE` | 200 | How many videos to poll each run |
| `TRACK_MAX_POLL_HOURS` | 24 | Time window for early metrics |
| `YT_API_KEY` | required | YouTube API key |
| `TRACK_API_COOLDOWN_SEC` | 5–30 | Adaptive sleep on quota hit |

Optional delete controls:

| Env | Default | Use |
|-----|---------|----|
| `TRACK_HARD_DELETE_NOPUB` | 1 | Immediately delete invalid docs |
| `TRACK_HARD_DELETE_UNAVAILABLE` | 1 | Immediately delete private/deleted |

These connect with `prune_unavailable_once.py` if separated workflow is preferred.

---

## ✅ Performance Characteristics

| Attribute | Result |
|----------|--------|
| Proven scale | 200k+ active videos |
| Minimal records scanned | uses tight polling filters |
| Fast writes | batch `bulk_write()` |
| Snapshot arrays remain small | ~dozens of entries per video |

---

## 🎯 Why This Matters

This tool produces **high-resolution temporal features** like:

- View velocity at T+1h, T+3h, T+6h
- Like-view ratios over time
- Engagement acceleration/deceleration

→ All crucial for **virality ML models**.

---

## ✔️ Recommended Ops Schedule

| Component | Frequency |
|----------|-----------|
| Track Once | Every 10–30 seconds |
| Pruner | Every 15–30 minutes |
| Archiver | Every 3–6 hours |

---

> `track_once.py` is the **signal engine** of the system.  
> The predictive accuracy of the entire project depends on the quality of snapshots it generates. 📈🔥

---

📅 **Last Updated:** **Oct 26 2025**

