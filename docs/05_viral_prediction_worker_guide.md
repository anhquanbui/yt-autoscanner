# Viral Prediction Workers Guide (Multiclass v2)

This document describes how to run and operate the **viral prediction pipeline**,
including the 6h / 12h / 24h scoring workers and the finalization worker.

The system predicts whether a YouTube video will become:
`non_viral`, `weak_viral`, `viral`, or `super_viral`.

---

## 1. Architecture Overview

The viral prediction pipeline is split into **two layers**:

### Core logic
- `viral_prediction_core.py`  
  Shared engine that:
  - Builds features from `stats_snapshots`
  - Runs multiclass XGBoost models
  - Writes results into `ml_flags.viral_v2.*`
  - Handles MongoDB access and safety checks

### Thin workers
- `viral_6h.py`   → early signal (6h)
- `viral_12h.py`  → confirmation (12h)
- `viral_24h.py`  → late validation (24h)
- `viral_finalize.py` → consolidate final decision

Each worker is intentionally small and delegates all heavy logic to the core.

---

## 2. Multiclass Labels

All viral models are **4-class classifiers**:

| Class ID | Label         | Meaning |
|--------|---------------|--------|
| 0 | non_viral   | No viral signal |
| 1 | weak_viral  | Mild / borderline virality |
| 2 | viral       | Clear viral trajectory |
| 3 | super_viral | Explosive / top-tier viral |

For each stage we store:
- Per-class probabilities
- Aggregated viral probability: `P(weak + viral + super)`
- `top_class` (argmax)

---

## 3. Time Stages

| Stage | Purpose | Mongo Path |
|-----|-------|-----------|
| 6h  | Early signal | `ml_flags.viral_v2.h6` |
| 12h | Confirmation | `ml_flags.viral_v2.h12` |
| 24h | Validator | `ml_flags.viral_v2.h24` |
| FINAL | Consolidation | `ml_flags.viral_v2.final` |

---

## 4. MongoDB Fields Written

### Per-stage (example: 6h)
```json
ml_flags.viral_v2.h6 = {
  "score_proba": 0.82,
  "score_100": 82,
  "top_class": "viral",
  "proba_non": 0.18,
  "proba_weak": 0.22,
  "proba_viral": 0.45,
  "proba_super": 0.15,
  "evaluated_at": "2025-01-01T12:00:00Z"
}
```

### Final decision
```json
ml_flags.viral_v2.final = {
  "status": "viral",
  "decided_stage": "24h",
  "score_proba": 0.91,
  "score_100": 91,
  "threshold_proba": 0.8,
  "threshold_100": 80,
  "behavior": "consistent",
  "reason": "viral_by_24h_validator",
  "decided_at": "2025-01-02T00:00:00Z"
}
```

---

## 5. Environment Variables

```env
MONGO_URI=mongodb://localhost:27017/ytscan
MONGO_DB=ytscan

VIRAL_MODEL_DIR=models/viral
VIRAL_MODEL_6H=models/viral/viral_xgb_6h.joblib
VIRAL_MODEL_12H=models/viral/viral_xgb_12h.joblib
VIRAL_MODEL_24H=models/viral/viral_xgb_24h.joblib
```

Optional thresholds:
```env
VIRAL_V2_THRESH_6H_PROBA=0.60
VIRAL_V2_THRESH_12H_PROBA=0.70
VIRAL_V2_THRESH_24H_PROBA=0.80
```

---

## 6. Running Individual Workers (Recommended)

### 6h Worker
```bash
python -m worker.viral_6h
```

- Only scores videos ≥ 6h old
- Skips already-scored videos
- Writes `ml_flags.viral_v2.h6`

---

### 12h Worker
```bash
python -m worker.viral_12h
```

- Requires video age ≥ 12h
- Writes `ml_flags.viral_v2.h12`

---

### 24h Worker
```bash
python -m worker.viral_24h
```

- Requires video age ≥ ~20–24h
- Writes `ml_flags.viral_v2.h24`

---

## 7. Finalization Worker

```bash
python viral_finalize.py --only-missing
```

### What it does
- Reads h6 / h12 / h24 results
- Applies stage priority: **24h → 12h → 6h**
- Handles special cases:
  - Low-quality blocked videos → `non_viral_lowq`
  - Removed videos → `viral_after_removed` / `removed`
- Attaches tracking metadata for auditing

---

## 8. Behavior Tags (Trajectory Analysis)

| Behavior | Meaning |
|-------|--------|
| no_signal | No stage crossed threshold |
| early_peak | Early viral, then fades |
| late_growth | Only becomes viral at 24h |
| consistent | Stable viral signal |
| volatile | Mixed / unstable trajectory |

These are useful for analytics and future training.

---

## 9. Safety & Idempotency

- Workers only update missing fields by default
- No document deletions
- Safe to re-run repeatedly
- Uses `worker_runs` collection for monitoring

---

## 10. Suggested Scheduling

| Worker | Frequency |
|------|----------|
| viral_6h | every 10–15 minutes |
| viral_12h | every 30 minutes |
| viral_24h | every 60 minutes |
| viral_finalize | every 1–2 hours |

---

## 11. Summary

- `viral_prediction_core.py` is the single source of truth
- Thin workers simplify ops and scheduling
- Multiclass predictions give richer signals than binary
- Finalization logic is explicit, auditable, and training-safe

---

**End of document**
