
# 🔥 Prune Unavailable Videos Tool  
**File:** `tools/prune_unavailable_once.py`

## 🎯 Purpose  
Immediately delete videos that:
- Became **Private / Deleted / Blocked**, or
- Lack a valid `publishedAt` timestamp

## Deletion Modes

| Mode | Default | Behavior |
|------|---------|----------|
| PASSIVE ✅ | enabled | Delete videos marked by tracker with: `stop_reason ∈ {"unavailable","no_publishedAt"}` |
| ACTIVE | disabled | Use YouTube API: if no `statistics` returned → delete |

## ⚙️ Environment Variables
| Env | Default | Description |
|-----|---------|-------------|
| `PRUNE_SOURCE` | videos | Collection to clean |
| `PRUNE_BATCH` | 2000 | Batch deletion |
| `PRUNE_DRY_RUN` | 1 | Show affected docs without deleting |
| `PRUNE_MIN_AGE_MIN` | 0 | Only delete older tracking failures |
| `PRUNE_ACTIVE_VERIFY` | 0 | Enable API validation |
| `YT_API_KEY` | — | Needed for active mode |

## 🚀 How to Run
Dry-run:
```bash
export PRUNE_DRY_RUN=1
python tools/prune_unavailable_once.py
```

Execute:
```bash
export PRUNE_DRY_RUN=0
python tools/prune_unavailable_once.py
```

Active check:
```bash
export PRUNE_ACTIVE_VERIFY=1
export YT_API_KEY=YOUR_KEY
python tools/prune_unavailable_once.py
```

---

📅 **Last Updated:** **Oct 26 2025**