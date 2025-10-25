
# 🎯 YouTube AutoScanner — VPS Deployment Edition

This repository powers an automated pipeline that:
✅ Discovers newly published YouTube videos  
✅ Tracks their performance in the first 24 hours  
✅ Cleans + processes analytics data  
✅ Exposes API endpoints for dashboards & BI tools  
✅ Operates continuously inside a Linux VPS

---

## 📂 Project Structure — VPS Version

```
yt-autoscanner/
│
├── api/                         # FastAPI backend
│   ├── main.py                  # Uvicorn server entry
│   └── requirements.txt         # API dependencies
│
├── tools/                       # Support / admin scripts
│   ├── auto_discover.sh         # Loop-discovery runner
│   ├── auto_track.sh            # Loop-tracking runner
│   ├── make_indexes.py          # MongoDB indexes
│   ├── backfill_channels.py
│   ├── backfill_missing_fields.py
│   └── mongo_backup.sh          # Scheduled DB backups
│
├── worker/                      # Ingestion + tracking logic
│   ├── discover_once.py         # Fetches new videos
│   ├── track_once.py            # Tracks analytics over time
│   ├── process_data.py          # Cleans & aggregates snapshots
│   └── requirements.txt         # Worker dependencies
│
├── mongo_backups/               # Automated backup storage
│
├── .venv/                       # Python virtual environment
├── .gitignore
├── CHANGELOG.md                 # Feature + version history
├── run_discover_once.sh         # Manual one-shot discover
```

---

## 🔑 Configuration

Environment variables must be set inside:

```
/home/ytscan/.env
```

Required:

```
YT_API_KEY=xxxx
MONGO_URI=mongodb://localhost:27017/autoscanner
DISCOVERY_MODE=random
```

Note: for authentication, please change MONGO_URI connection string

---

## 🚀 Services Running on VPS

| Component | Method | Status Cmd |
|----------|--------|------------|
| Discover Worker | systemd | `systemctl status yt-auto-discover` |
| Track Worker | systemd | `systemctl status yt-auto-track` |
| Backups | cronjob | `crontab -l` |
| API (FastAPI + Uvicorn) | systemd | `systemctl status yt-api` |

Restart services:

```
sudo systemctl restart yt-auto-discover yt-auto-track
```

Note: please put away unavailable service

---

## 📊 MongoDB Collections

| Collection | Purpose |
|-----------|---------|
| `videos` | All discovered YouTube videos |
| `processed_videos` | Aggregated analytics + horizon stats |
| `channels` | Channels metadata |
| `dashboard_summary` | Cached BI metrics |

Indexes are created via:

```
python tools/make_indexes.py
```

---

## 🔄 Automatic Processing Intervals

| Script | Interval | Purpose |
|--------|----------|---------|
| `discover_once.py` | 5 min | New upload detection |
| `track_once.py` | 15 sec | 1h→24h early-signal tracking |
| `process_data.py` | 6 hours | BI metric refresh | (not deployed yet)

---

## 🔍 Monitoring & Debugging

### Worker logs
```
journalctl -u yt-auto-discover -f
journalctl -u yt-auto-track -f
```

### Check running processes
```
htop
```

---

## 🧠 Data Flow (Quick View)

```
Discover → Insert video → Track stats → Process → Dashboards
```

This smooth pipeline powers real-time YouTube early-signal analytics.

---

## 🧩 Technology Stack

✅ Python (FastAPI + Workers)  
✅ MongoDB  
✅ systemd automation  
✅ CRON backup  
✅ Linux VPS (Cloudflare Tunnel supported)

---

### ✨ Author
Developed by **Anh Quan Bui**  
(Post-Graduate Certificate — Data Analytics & AI)

---

📌 This is the **official deployment structure** currently used on the VPS