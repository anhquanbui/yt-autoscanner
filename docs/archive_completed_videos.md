
# 🧊 Archive Completed Videos Tool  
**File:** `tools/archive_completed_videos.py`

## 🎯 Purpose  
Move videos that **finished early-engagement tracking** out of the hot collection `videos`, into lightweight **cold storage**, to reduce load and improve performance.

Workflow:
1. Select documents where `tracking.status = "complete"`
2. Insert into a **partitioned cold collection** (monthly by default)
3. Delete from `videos` once successful

## 🧩 Partitioning Strategy  
Default: `month`  
→ Automatically generates collections such as:  
`videos_cold_2025_10`, `videos_cold_2025_11`, …

Alternative modes:
| Mode | Description |
|------|-------------|
| `month` ✅ | Create monthly partitions (recommended) |
| `year` | Partition by year |
| `flat` | One single cold collection |
| `rollover` | Split into buckets when a size limit is reached |

## 🪶 Data Trimming  
To reduce cold storage footprint, the tool can strip:
- `snippet.thumbnails`
- `description` (if present)

## ⚙️ Environment Variables
| Env | Default | Description |
|-----|---------|-------------|
| `ARCHIVE_SRC` | videos | Source collection |
| `ARCHIVE_DST` | videos_cold | Base name for cold partitions |
| `ARCHIVE_PARTITION` | month | Partitioning rule |
| `ARCHIVE_BATCH` | 2000 | Batch size per write |
| `ARCHIVE_MIN_AGE_HOURS` | 0 | Only archive if last polled before cutoff |
| `ARCHIVE_DRY_RUN` | 1 | Dry-run mode |
| `ARCHIVE_TRIM_FIELDS` | 1 | Trim heavy fields |

## 🚀 How to Run
Dry-run:
```bash
export ARCHIVE_DRY_RUN=1
python tools/archive_completed_videos.py
```

Execute:
```bash
export ARCHIVE_DRY_RUN=0
python tools/archive_completed_videos.py
```

---

📅 **Last Updated:** **Oct 26 2025**