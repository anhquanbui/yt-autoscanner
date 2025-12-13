# Low-Quality Auto-Flag Workers Guide

This document explains how to use the **Low-Quality Auto-Flag pipeline** for YouTube videos, including the shared core logic and the dedicated 3h / 6h workers.

---

## 1. Overview

The low-quality pipeline is designed to automatically detect and stop **low-potential videos early**, using machine learning models at different time horizons.

It consists of:

- **Core logic**
  - `low_quality_core.py` – shared implementation for feature extraction, scoring, MongoDB updates, and CLI handling

- **Thin worker wrappers**
  - `low_quality_3h_worker.py` – runs only the 3-hour model (videos aged 3–6h)
  - `low_quality_6h_worker.py` – runs only the 6-hour model (videos aged ≥6h)

This design keeps all business logic in one place while allowing simple, explicit workers for scheduling and operations.

---

## 2. Processing Logic (High Level)

### Time Windows

| Model | Video Age | Purpose |
|-----|----------|--------|
| **3h model** | 3h ≤ age < 6h | Early elimination of weak videos |
| **6h model** | age ≥ 6h | Stronger signal with more engagement data |

### Core Flow

1. Query candidate videos from MongoDB.
2. Compute time-based features from `stats_snapshots`.
3. Run ML inference (3h and/or 6h).
4. Write results into `ml_flags.low_quality_*`.
5. Optionally stop tracking if the video is classified as low quality.

---

## 3. MongoDB Fields Written

### 3-Hour Results
```json
ml_flags.low_quality_v1_3h = {
  "score": <float>,
  "threshold": <float>,
  "is_low": <bool>,
  "updated_at": <ISO-8601 UTC timestamp>
}
```

### 6-Hour Results
```json
ml_flags.low_quality_v3_6h = {
  "score": <float>,
  "threshold": <float>,
  "is_low": <bool>,
  "updated_at": <ISO-8601 UTC timestamp>
}
```

### Optional Tracking Stop
If enabled and the current status is `"tracking"`:
```json
tracking.status = "stopped"
tracking.stop_reason = "ml.low_quality_v1_3h" | "ml.low_quality_v3_6h"
```

---

## 4. Environment Variables

Minimum required variables (usually set in `.env`):

```env
MONGO_URI=mongodb://localhost:27017/ytscan
MONGO_DB=ytscan

LOWQ_MODEL_3H_PATH=/models/lowq_3h.joblib
LOWQ_MODEL_6H_PATH=/models/lowq_6h.joblib
```

Optional overrides:

```env
LOWQ_THRESHOLD_3H=0.40
LOWQ_THRESHOLD_6H=0.33
LOWQ_BATCH_SIZE=500
```

---

## 5. Running the Core Worker (CLI)

Run both 3h and 6h logic:
```bash
python low_quality_core.py --mode both
```

Run 3h-only:
```bash
python low_quality_core.py --mode 3h-only --only-missing
```

Run 6h-only:
```bash
python low_quality_core.py --mode 6h-only --only-missing
```

---

## 6. Dedicated Workers (Recommended)

### 3h Worker
```bash
python -m worker.low_quality_3h_worker
```

### 6h Worker
```bash
python -m worker.low_quality_6h_worker
```

---

## 7. Backfill Mode (Offline / Training)

```bash
python low_quality_core.py --backfill-all-scores
```

---

## 8. Deployment Recommendation

| Worker | Schedule |
|------|---------|
| 3h worker | every 10–15 minutes |
| 6h worker | every 30–60 minutes |

---

## 9. Summary

- Core logic lives in `low_quality_core.py`
- Thin wrappers simplify scheduling
- Safe to rerun and idempotent
- Designed for production use
