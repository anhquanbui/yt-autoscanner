# make_indexes_v2.py — Smart MongoDB Index Manager

**Last updated:** 2025-10-22 21:45:40

---

## 📘 Overview
`make_indexes_v2.py` is an enhanced MongoDB index management tool designed for projects with multiple collections such as **videos**, **channels**, and **processed**.  
It automatically creates, verifies, and (optionally) removes outdated indexes, ensuring optimal query performance across the system.

---

## ⚙️ Key Features

| Feature | Description |
|----------|--------------|
| ✅ Multi-collection support | Automatically handles `videos`, `channels`, and `processed` collections. |
| ⚡ Duplicate-safe | Checks for existing indexes before creating new ones. |
| 🧹 Cleanup mode | Optional flag `--drop-old` removes indexes not in the official map. |
| 👀 Preview mode | `--show-only` displays planned actions without applying changes. |
| 🧾 Logging | Writes all actions to both console and `index_maintenance.log`. |
| 🚀 Background creation | Indexes are created asynchronously, minimizing downtime. |

---

## 🧱 Default Index Map

### `videos` collection
| Purpose | Fields |
|----------|---------|
| Tracking scheduler | `tracking.status`, `tracking.next_poll_after` |
| Sort by publish date | `snippet.publishedAt` |
| Region-based filter | `source.regionCode` |
| Channel grouping | `snippet.channelId` |
| Duration classification | `snippet.lengthBucket` |
| Category analytics | `snippet.categoryId` |

### `channels` collection
| Purpose | Fields |
|----------|---------|
| Primary key | `_id` |
| Channel handle lookup | `handle` |
| Detect stale channels | `last_checked_at` |

### `processed` collection
| Purpose | Fields |
|----------|---------|
| Video reference | `video_id` |
| Query by status | `status` |
| Snapshot ordering | `snapshot_time` |

---

## 🧩 Command-Line Options

| Flag | Description |
|------|--------------|
| `--show-only` | Displays which indexes would be created or removed, without modifying the database. |
| `--drop-old` | Drops indexes that are not defined in `INDEX_MAP`. |
| `--collections` | Comma-separated list of collections to process. Default: all. |

---

## 🧠 Usage Examples

```bash
# Create all standard indexes
python make_indexes_v2.py

# Preview actions only (no DB changes)
python make_indexes_v2.py --show-only

# Clean up unused indexes and rebuild required ones
python make_indexes_v2.py --drop-old

# Apply only to specific collections
python make_indexes_v2.py --collections videos,channels
```

---

## 🪵 Logging Output

The script automatically logs every action to a file named **`index_maintenance.log`**, stored in the same directory.  
Example output:

```
[2025-10-22 15:42:12] 📂 Collection: videos
[2025-10-22 15:42:12] ✅ Created index: [('tracking.status', 1), ('tracking.next_poll_after', 1)]
[2025-10-22 15:42:13] ⏭️  Skipped existing index: [('snippet.publishedAt', -1)]
```

---

## 🧰 Safety Notes

- Safe to run multiple times — existing indexes are skipped automatically.
- Creating indexes uses **background mode**, so it will not block reads/writes.
- When `--drop-old` is used, only non-standard indexes are removed (the default `_id_` index is always preserved).

---

## 📦 Requirements

- Python ≥ 3.8
- `pymongo` package installed (`pip install pymongo`)
- Access to a valid MongoDB instance via `MONGO_URI` environment variable

---

## 🏁 Example Integration

You can schedule this script to run weekly using **cron** or **systemd timer** for automatic index maintenance.

Example cron entry (runs every Sunday at 3AM):

```
0 3 * * SUN /usr/bin/python3 /path/to/make_indexes_v2.py --drop-old >> /var/log/mongo_index.log 2>&1
```

---

## ✍️ Author Notes

This script was designed for advanced MongoDB workflows such as YouTube data tracking, analytics, and backfill pipelines.  
It can be extended to include dashboard-specific indexes (e.g. sorting by view count or likes).

---
