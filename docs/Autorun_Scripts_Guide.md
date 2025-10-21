# ⚙️ Autorun Scripts — YouTube Auto Scanner

Two PowerShell scripts automate the combined workflow for **discovering** and **tracking** YouTube videos using the `yt-autoscanner` project.  
They include environment loading, structured logging, auto-retry logic, and cooldowns for quota exhaustion.

---

## 🧩 1. `run_both_local.ps1`

**Purpose:**  
Runs **both stages** in one continuous loop:
- `discover_once.py` → discovers new videos
- `track_once.py` → tracks metrics for discovered videos

**Key Features:**
- Auto-loads `.env` (no hardcoded secrets)
- Logs output to console and `logs/scanner-YYYY-MM-DD.log`
- Automatically creates `logs/` folder if missing
- Runs continuously with staggered intervals
- Quota exhaustion → 15-minute cooldown

**Default Intervals:**

| Task | Interval | Script |
|------|-----------|---------|
| Discover | 1800 seconds (30 minutes) | `worker/discover_once.py` |
| Track | 30 seconds | `worker/track_once.py` |

**Usage:**
```powershell
# 1. Open PowerShell in the repo root (replace your PATH)
cd D:\PYTHON\PROJECT\yt-autoscanner

# 2. Allow temporary execution of unsigned scripts
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 3. Run autorun
.\run_both_local.ps1
```

**Logs:**
- Main logs: `logs/scanner-YYYY-MM-DD.log`
- Transcript logs: `logs/transcript-YYYY-MM-DD.log`

**Stop Script:** Press `Ctrl + C` or close PowerShell window.

---

## 🔁 2. `run_track_one_loop_30s.ps1`

**Purpose:**  
Runs **only the tracker (`track_once.py`)** repeatedly at fixed intervals (default: 30 seconds).

**Key Features:**
- Loads `.env` automatically
- Logs to `logs/track-YYYY-MM-DD.log`
- Handles quota exhaustion (exit code `88`) with cooldown
- Stops gracefully on any error (exit code ≠ 0,88)

**Usage:**
```powershell
cd D:\PYTHON\PROJECT\yt-autoscanner #replace with your PATH
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Default loop (every 30s, 15min cooldown on quota)
.\run_track_one_loop_30s.ps1
```

**Optional Parameters:**

| Parameter | Default | Description |
|------------|----------|-------------|
| `-Loop` | `true` | Enables continuous looping |
| `-IntervalSeconds` | `30` | Delay between successful runs |
| `-CooldownSeconds` | `900` | Wait time (15 min) after quota exhaustion |
| `-PythonExe` | `"python"` | Override to `"py"` if needed |

**Examples:**
```powershell
# Run every 60s, 20min cooldown on quota exhaustion
.
un_track_one_loop_30s.ps1 -Loop -IntervalSeconds 60 -CooldownSeconds 1200
```

**Logs:**
- Runtime log: `logs/track-YYYY-MM-DD.log`
- Console displays real-time activity

**Stop Script:** Press `Ctrl + C` or close PowerShell window.

---

## 📁 Recommended Directory Structure

```
yt-autoscanner/
│
├── worker/
│   ├── discover_once.py
│   ├── track_once.py
│
├── logs/
│   ├── scanner-2025-10-18.log
│   ├── track-2025-10-18.log
│
├── .env
├── run_both_local.ps1
└── run_track_one_loop_30s.ps1
```

---

## 💡 Tips

- Always run scripts in **PowerShell**, not CMD.
- You can automate startup using **Windows Task Scheduler** or **pm2-windows-startup**.
- To tail logs in real-time:
  ```powershell
  Get-Content .\logs\track-2025-10-18.log -Wait
  ```

---

📅 **Last Updated:** **Oct 20 2025**