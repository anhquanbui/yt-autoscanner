
# run_scheduler.ps1 - Activates venv and starts scheduler (30s loop)
param(
  [int]$IntervalSeconds = 30
)

# Move to project root (EDIT this path if your repo lives elsewhere)
$ProjectRoot = "D:\PROJECT\yt-autoscanner"

Set-Location $ProjectRoot

# Activate venv
.\venv\Scripts\Activate.ps1

# ---- Env for worker (adjust as you like) ----
$env:YT_API_KEY = $env:YT_API_KEY  # reuse what's set globally, or set here
$env:MONGO_URI  = "mongodb://localhost:27017/ytscan"
$env:YT_REGION  = "US"
$env:YT_LOOKBACK_MINUTES = "90"     # smaller lookback for frequent polling
Remove-Item Env:YT_CHANNEL_HANDLE,Env:YT_CHANNEL_ID,Env:YT_FILTER_CATEGORY_ID,Env:YT_TOPIC_ID,Env:YT_QUERY -ErrorAction SilentlyContinue

# scheduler interval
$env:DISCOVER_INTERVAL_SECONDS = [string]$IntervalSeconds

# Start
python .\worker\scheduler.py
