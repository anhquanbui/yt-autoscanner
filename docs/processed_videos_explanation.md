# Explanation of processed_videos.json Example

This document explains the meaning of each field in a sample entry from `processed_videos.json`.

---

## 🎬 Example Entry
```json
{
  "video_id": "abcd1234",
  "status": "complete",
  "published_at": "2025-10-17T03:25:00Z",
  "n_snapshots": 78,
  "last_snapshot_ts": "2025-10-17T23:20:00Z",
  "horizons": {
    "60":   { "views": 1200, "likes": 70,  "comments": 4,  "value_method": "floor", "coverage_ratio": 0.62, "n_expected": 12, "n_available": 8 },
    "180":  { "views": 4200, "likes": 190, "comments": 21, "value_method": "floor", "coverage_ratio": 0.60, "n_expected": 28, "n_available": 17 },
    "360":  { "views": 9200, "likes": 440, "comments": 48, "value_method": "ceil",  "coverage_ratio": 0.51, "n_expected": 44, "n_available": 22 },
    "1440": { "views": 56000,"likes": 2300,"comments": 380,"value_method": "floor", "coverage_ratio": 0.92, "n_expected": 80, "n_available": 74 }
  }
}
```

---

## 🧩 Top-Level Fields

| Field | Description |
|--------|--------------|
| `video_id` | The unique YouTube video ID being tracked. |
| `status` | `"complete"` means the tracking finished successfully (data collection reached 24h). |
| `published_at` | The exact UTC timestamp when the video was published. |
| `n_snapshots` | Number of API snapshots actually collected (each snapshot = one poll). |
| `last_snapshot_ts` | Timestamp of the last data point recorded for this video. |

---

## 🕓 Horizons (Time Milestones)

Each key inside `horizons` corresponds to a **time horizon in minutes** after publication:

| Key | Horizon | Description |
|-----|----------|--------------|
| `60` | 1 hour | Early stage metrics |
| `180` | 3 hours | Short-term trend |
| `360` | 6 hours | Mid-term growth |
| `1440` | 24 hours | Full-day performance |

Each horizon contains detailed data and tracking coverage info.

---

### 🔸 Horizon Example (1 hour)
```json
"60": { "views": 1200, "likes": 70, "comments": 4, "value_method": "floor", "coverage_ratio": 0.62, "n_expected": 12, "n_available": 8 }
```

**Interpretation:**
- After 1 hour, the video had **1,200 views**, **70 likes**, and **4 comments**.
- `"value_method": "floor"` → the metric was taken from the **last snapshot before or exactly at 1h mark**.
- `"coverage_ratio": 0.62"` → 62% of expected snapshots were successfully collected.
- `"n_expected": 12"` → 12 polls expected (every 5 minutes × 60min).
- `"n_available": 8"` → only 8 snapshots collected.

---

### 🔸 Horizon Example (6 hours)
```json
"360": { "views": 9200, "likes": 440, "comments": 48, "value_method": "ceil", "coverage_ratio": 0.51, "n_expected": 44, "n_available": 22 }
```
**Interpretation:**
- After 6 hours: **9,200 views**, **440 likes**, **48 comments**.
- `"value_method": "ceil"` → since no data existed at or before 6h, the first snapshot **after 6h (within +30min)** was used.
- `"coverage_ratio": 0.51"` → only about half the expected snapshots were collected by that time.

---

### 🔸 Horizon Example (24 hours)
```json
"1440": { "views": 56000, "likes": 2300, "comments": 380, "value_method": "floor", "coverage_ratio": 0.92, "n_expected": 80, "n_available": 74 }
```
**Interpretation:**
- After 24h, the video reached **56,000 views**, **2,300 likes**, **380 comments**.
- Data taken from the last snapshot before 24h (`"floor"`).
- 74 out of 80 expected snapshots → 92% data coverage (excellent).

---

## 📊 Summary Table

| Horizon | Time | Views | Likes | Comments | Method | Coverage | Expected | Available |
|----------|------|-------|--------|-----------|----------|-----------|-----------|
| 60 min | 1h | 1,200 | 70 | 4 | floor | 0.62 | 12 | 8 |
| 180 min | 3h | 4,200 | 190 | 21 | floor | 0.60 | 28 | 17 |
| 360 min | 6h | 9,200 | 440 | 48 | ceil | 0.51 | 44 | 22 |
| 1440 min | 24h | 56,000 | 2,300 | 380 | floor | 0.92 | 80 | 74 |

---

## 💡 Key Insights

- This video had **strong early growth** (1.2K → 56K views in 24h).  
- Data quality improved over time (coverage from 0.62 → 0.92).  
- `"floor"` values are generally more accurate since they use direct prior snapshots.  
- `"ceil"` is used when data is slightly delayed (late polling).  
- The entire record can be used for machine learning, data visualization, or anomaly detection.

---

**Last updated:** 2025-10-17
