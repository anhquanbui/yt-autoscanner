<#
 run_discover_loop.ps1
 ---------------------
 Runs the YouTube discover worker in a loop (default every 30s).
 Edit the "CONFIG" section below (or pass parameters) — each line says what you can change.

 USAGE (PowerShell):
   .\run_discover_loop.ps1                         # uses defaults
   .\run_discover_loop.ps1 -IntervalSeconds 60     # override interval
   .\run_discover_loop.ps1 -RandomMode $true       # enable random mode
   .\run_discover_loop.ps1 -ProjectRoot "D:\PROJECT\yt-autoscanner"

 Tip: Keep a separate terminal open for the API (uvicorn).
#>

param(
  # How often to run discover_once (seconds). CHANGE if you want less/more frequent.
  [int]$IntervalSeconds = 30,

  # Path to your project root (folder containing \worker and \venv). CHANGE if different.
  [string]$ProjectRoot = "D:\PROJECT\yt-autoscanner",

  # --- Random Mode toggles ---
  # Enable random mode (pick random time slice/region/query each run). Toggle $true/$false.
  [bool]$RandomMode = $true,
  # How far back random can pick (minutes). Example: 43200 = 30 days.
  [int]$RandomLookbackMinutes = 43200,
  # Width of random time slice (minutes). Example: 30 = half-hour window.
  [int]$RandomWindowMinutes = 30,

  # Pools for random region/keyword. Comma-separated. Leave empty to disable.
  [string]$RandomRegionPool = "US,GB,JP,VN",
  [string]$RandomQueryPool  = "gaming,stream,highlights,,",

  # --- Deterministic Search (used when RandomMode=$false) ---
  # Default region and lookback window (minutes). CHANGE if you want a wider/narrower window.
  [string]$Region = "US",
  [int]$LookbackMinutes = 360,

  # Optional fixed filters (leave blank to disable each one)
  [string]$Query        = "",         # e.g. "gaming"
  [string]$ChannelId    = "",         # e.g. "UCxxxxxxxxxxxx"
  [string]$ChannelHandle= "",         # e.g. "@SomeChannel"
  [string]$TopicId      = "",         # e.g. "/m/0bzvm2" for Gaming
  [string]$CategoryId   = "",         # e.g. "20" for Gaming

  # Mongo + API key. You can keep these empty to reuse global env already set in this session.
  [string]$MongoUri     = "mongodb://localhost:27017/ytscan",
  [string]$ApiKey       = ""          # Put your API key here if you don't have it in $env:YT_API_KEY
)

# ===== CONFIG (what you can change) =====
# - IntervalSeconds: change frequency for the loop
# - ProjectRoot: point to your repo path
# - RandomMode & its params: toggle and tune random behavior
# - Region/Lookback/Query: tune default search when RandomMode is OFF
# - ChannelId/ChannelHandle/TopicId/CategoryId: add/remove filters
# - MongoUri/ApiKey: set if not using pre-set $env variables
# =======================================

# Move to project root
Set-Location $ProjectRoot

# Activate venv (edit this path if your venv lives elsewhere)
$venv = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $venv) {
  . $venv
} else {
  Write-Warning "Could not find venv at $venv. Make sure your virtualenv exists."
}

# Ensure UTF-8 output (prevents emoji logging issues)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# --- REQUIRED: YouTube API key ---
if ([string]::IsNullOrWhiteSpace($env:YT_API_KEY)) {
  if (-not [string]::IsNullOrWhiteSpace($ApiKey)) {
    $env:YT_API_KEY = $ApiKey
  } else {
    Write-Error "YT_API_KEY is not set. Pass -ApiKey or set `$env:YT_API_KEY first."
    exit 1
  }
}

# --- Mongo connection (can override here) ---
if (-not [string]::IsNullOrWhiteSpace($MongoUri)) {
  $env:MONGO_URI = $MongoUri
}

# --- Interval for the loop ---
$env:DISCOVER_INTERVAL_SECONDS = [string]$IntervalSeconds

# --- Random mode envs (used by v3 discover) ---
if ($RandomMode) {
  $env:YT_RANDOM_MODE = "1"
  $env:YT_RANDOM_LOOKBACK_MINUTES = [string]$RandomLookbackMinutes
  $env:YT_RANDOM_WINDOW_MINUTES   = [string]$RandomWindowMinutes

  if (-not [string]::IsNullOrWhiteSpace($RandomRegionPool)) {
    $env:YT_RANDOM_REGION_POOL = $RandomRegionPool
  } else {
    Remove-Item Env:YT_RANDOM_REGION_POOL -ErrorAction SilentlyContinue
  }

  if (-not [string]::IsNullOrWhiteSpace($RandomQueryPool)) {
    $env:YT_RANDOM_QUERY_POOL = $RandomQueryPool
  } else {
    Remove-Item Env:YT_RANDOM_QUERY_POOL -ErrorAction SilentlyContinue
  }
} else {
  # Deterministic mode
  $env:YT_RANDOM_MODE = "0"
  $env:YT_REGION = $Region
  $env:YT_LOOKBACK_MINUTES = [string]$LookbackMinutes

  if ([string]::IsNullOrWhiteSpace($Query))        { Remove-Item Env:YT_QUERY -ErrorAction SilentlyContinue }        else { $env:YT_QUERY = $Query }
  if ([string]::IsNullOrWhiteSpace($ChannelId))    { Remove-Item Env:YT_CHANNEL_ID -ErrorAction SilentlyContinue }   else { $env:YT_CHANNEL_ID = $ChannelId }
  if ([string]::IsNullOrWhiteSpace($ChannelHandle)){ Remove-Item Env:YT_CHANNEL_HANDLE -ErrorAction SilentlyContinue }else { $env:YT_CHANNEL_HANDLE = $ChannelHandle }
  if ([string]::IsNullOrWhiteSpace($TopicId))      { Remove-Item Env:YT_TOPIC_ID -ErrorAction SilentlyContinue }      else { $env:YT_TOPIC_ID = $TopicId }
  if ([string]::IsNullOrWhiteSpace($CategoryId))   { Remove-Item Env:YT_FILTER_CATEGORY_ID -ErrorAction SilentlyContinue } else { $env:YT_FILTER_CATEGORY_ID = $CategoryId }
}

# Make sure logs dir exists (scheduler also ensures this)
$logs = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

# Run the scheduler loop (calls worker\discover_once.py repeatedly)
$script = Join-Path $ProjectRoot "worker\scheduler.py"
if (-not (Test-Path $script)) {
  Write-Error "Cannot find $script. Ensure worker\scheduler.py exists."
  exit 1
}
Write-Host "Starting scheduler (interval=$IntervalSeconds s). Press Ctrl+C to stop." -ForegroundColor Cyan
python -u $script
