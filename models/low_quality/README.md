# Low Quality Detection Model — v3 (6h)

## 📘 Overview
The **Low Quality v3 (6h)** model is an XGBoost binary classifier designed to predict whether a YouTube video is likely to be *low-performing* within the first **6 hours** after upload.  
It operates as part of the `yt-autoscanner` pipeline, helping to automatically flag low-quality or stagnant videos for early exclusion in data tracking and downstream model training.

---

## ⚙️ Model Information

| Attribute | Description |
|------------|-------------|
| **Model file** | `low_quality_model_v3_6h_xgb.joblib` |
| **Threshold file** | `low_quality_threshold_v3_6h_xgb.json` |
| **Best threshold** | `0.831556499004364` |
| **Framework** | XGBoost |
| **Task type** | Binary classification (`is_low_quality`) |
| **Prediction window** | 6 hours since initial discovery |
| **Labeling rule** | `(growth < 1.3) and (views_last < 500)` |
| **Training dataset** | Derived from `processed_videos.parquet` with milestone stats (1h → 24h) |
| **Feature group** | Snapshot features, engagement ratios, coverage ratios, activity meta-features |

---

## 🧩 Output Schema

Each document in the `videos` collection will contain an updated `ml_flags.low_quality_v3_6h` structure:

```json
"ml_flags": {
  "low_quality_v3_6h": {
    "is_low": true,
    "score": 0.921,
    "threshold": 0.831556499004364,
    "updated_at": "2025-11-11T18:45:00Z"
  }
}
```

---

## 🧠 Inference Pipeline

### Load Model & Threshold
```python
import joblib, json
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "low_quality"
model = joblib.load(MODEL_DIR / "low_quality_model_v3_6h_xgb.joblib")
threshold = json.load(open(MODEL_DIR / "low_quality_threshold_v3_6h_xgb.json"))["best_threshold"]
```

### Apply to Data
```python
proba = model.predict_proba(df[feature_cols])[:, 1]
df["is_low"] = (proba >= threshold)
```

### Update MongoDB
```python
from datetime import datetime
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/ytscan")
db = client["ytscan"]

for vid, p in zip(df["video_id"], proba):
    db.videos.update_one(
        {"_id": vid},
        {"$set": {
            "ml_flags.low_quality_v3_6h": {
                "is_low": bool(p >= threshold),
                "score": float(p),
                "threshold": threshold,
                "updated_at": datetime.utcnow().isoformat()
            }
        }}
    )
```

---

## 🧾 Version Notes

| Version | Date | Notes |
|----------|------|-------|
| **v3 (6h)** | Nov 2025 | Improved feature set and stricter threshold for precision-oriented filtering. |
| **v2 (24h)** | Aug 2025 | Previous iteration trained on 24-hour metrics. |
| **v1 (beta)** | Jun 2025 | Initial proof-of-concept for low-quality flagging. |

---

## 🧮 Integration Summary
- **Used by:** `tools/low_quality_filter.py`, `worker/track_once.py`
- **Flag written to:** `videos.ml_flags.low_quality_v3_6h`
- **Purpose:** Early termination of low-growth videos to optimize resource allocation in scanning and viral prediction tasks.

---

## 🔒 Notes
- This model is **idempotent**: re-evaluating the same video will produce the same flag unless new data (e.g., updated views) is provided.  
- Threshold tuning was performed using F1-optimization on validation folds (6h horizon).  
- Always pair the `.joblib` model with its corresponding `.json` threshold to ensure consistent decision logic.

---

**Author:** A. Quan Bui  
**Project:** YouTube Video Virality & Quality Detection — `yt-autoscanner`
