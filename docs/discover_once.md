# 🔎 Discover Once — New Video Discovery Tool  
**File:** `tools/discover_once.py`

## 🎯 Purpose
Discover newly published YouTube videos and insert them into MongoDB for later polling by `track_once.py`.

Discovery is optimized to:
- Capture **fresh** videos (within a near-now window)
- Maintain **lightweight** records (no heavy metadata)
- Support **random exploration** with weighted keywords & region pools

---

## 📌 What It Saves to MongoDB

| Field Group | Details |
|------------|---------|
| Identity | `_id` = YouTube videoId |
| Publication | `snippet.publishedAt` |
| Channel | `snippet.channelId` *(channelTitle removed to reduce size)* |
| Tracking bootstrap | `tracking.status="tracking"`, `poll_count=0`, timestamps |
| Metadata (light) | `categoryId` |
| Video Duration | `durationISO`, `durationSec`, `lengthBucket` |

---

## 🧹 What It **Does Not** Save (by design)

| Field | Reason |
|-------|--------|
| channelTitle, channelHandle | Not needed for tracking logic |
| statistics (views/likes/comments) | Tracker handles these snapshots |
| thumbnails / descriptions | Too heavy, not needed early-stage |
| livestream or scheduled streams | Skip (inaccurate early statistics) |

This ensures the `videos` collection remains fast and compact.

---

## 🚦 Filtering Rules

| Filter | Condition |
|--------|-----------|
| Too old videos | Excluded via `publishedAt >= now - SINCE_MINUTES` |
| Live / Upcoming videos | Skipped entirely |
| Missing publishedAt | Skipped to avoid garbage insertion |
| Duplicate IDs | Ignored via upsert |

---

## 🧠 Discovery Modes

### 1) SINCE Mode (Deterministic)
Search recent videos from a fixed “lookback window”

Used when:
- `YT_RANDOM_PICK=0`

Example:
```
YT_SINCE_MINUTES=10
```

---

### 2) RANDOM Mode (Exploratory / Weighted Sampling)
Weighted random sampling over:
- Region pool (e.g., `US,CA,GB,...`)
- Keyword pool (with assigned weights)

Enabled when:
```
YT_RANDOM_PICK=1
```

High-weight keywords → discovered more often  
→ increases variety & category coverage

---

## ⚙️ Environment Variables

| Env | Default | Description |
|-----|---------|-------------|
| `YT_SINCE_MINUTES` | 10 | How recent to fetch if not random |
| `YT_MAX_PAGES` | 3 | Pages per API query |
| `YT_RANDOM_PICK` | 1 | Use random mode |
| `YT_RANDOM_REGION_POOL` | Predefined large set | Region diversity |
| `YT_RANDOM_QUERY_POOL` | Large weighted pool | Category diversity |

---

## 📦 Output Summary

Every discovered video doc:
- Starts life as **tracking**
- Will be consumed by `track_once.py`
- May later be archived or pruned depending on outcome

---

## ✅ Benefits

| Benefit | Impact |
|--------|--------|
| Lightweight records | Faster ingestion & smaller indexes |
| Configurable exploration | Better dataset diversity |
| Robust filters | Less garbage data & reprocessing |
| Pre-enriched duration/category | Less API work for tracker |

---

## 🧩 Example Discovery Flow Diagram

```text
Discover Once
   ↓ inserts
[ videos ] (tracking status)
   ↓ track polling (until 24h)
Track Once
   ↓
Completed videos → Archive (cold)
Unavailable videos → Prune
```

---

## 📝 Notes for Collaborators

- Don’t add **statistics** here → tracking must own stats timeline
- Don’t reintroduce titles/handles → significant bloat
- Tune region & query pools to cover trending discovery gaps

---

> The discovery stage sets the **foundation** for high-quality early-engagement datasets.  
> Keep it **fast**, **clean**, and **targeted**. 🚀

---

📅 **Last Updated:** **Oct 26 2025**