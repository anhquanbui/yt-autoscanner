<# 
  run_both_local.ps1 — Local runner for discover_once.py + track_once.py
  - Sets environment variables locally (PowerShell session scope)
  - Runs discover (near-now scan with random region + weighted keywords) and tracker in a timed loop
  - Logs to file AND to console (daily-rotated log files)
#>

# -------------------------------
# 🔧 Editable settings (local)
# -------------------------------

# Python & project paths
$PythonExe   = "python"                    # or "py" if you prefer
$RepoRoot    = (Get-Location)              # repo root; script assumes worker/ under this path
$WorkerDir   = Join-Path $RepoRoot "worker"

# Intervals
$DiscoverIntervalSeconds = 1800            # 30 minutes between discover runs
$TrackIntervalSeconds    = 30             # 5 minutes between track runs
$TickSleepSeconds        = 5               # main loop tick sleep

# Logging
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
function Get-LogFile {
    $date = Get-Date -Format "yyyy-MM-dd"
    return Join-Path $LogDir "scanner-$date.log"
}
function Write-Log([string]$msg, [string]$level="INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$level] $msg"
    Write-Host $line
    $line | Out-File -FilePath (Get-LogFile) -Append -Encoding utf8
}

# -------------------------------
# 🌐 Environment variables (local)
# -------------------------------

# Core (must set your own API key & Mongo URI)
$env:YT_API_KEY = $env:YT_API_KEY  # if already set in Windows user env, it will be used
if (-not $env:YT_API_KEY) {
    # TODO: put your key here or set from Windows env before running
    $env:YT_API_KEY = "PUT_YOUR_YOUTUBE_API_KEY_HERE"
}
$env:MONGO_URI = $env:MONGO_URI
if (-not $env:MONGO_URI) {
    $env:MONGO_URI = "mongodb://localhost:27017/ytscan"
}

# Discover behavior: near-now window
$env:YT_SINCE_MINUTES = "20"   # 20 minutes near-now
$env:YT_MAX_PAGES     = "1"    # ~101 quota per run

# Random region + weighted keywords
$env:YT_RANDOM_PICK        = "1"
$env:YT_RANDOM_REGION_POOL = "US,GB,CA,AU,IN,JP,VN,KR,FR,DE,BR,MX,ID,TH,ES,IT"

# Global weighted pool (term:weight, comma-separated)
$env:YT_RANDOM_QUERY_POOL  = "live:6, breaking news:5, news:5, update:3, gaming:6, highlights:6, esports:4, fortnite:5, minecraft:5, roblox:4, valorant:5, league of legends:5, genshin impact:4, pubg:3, mobile legends:3, fifa:4, nba:4, nfl:4, soccer:5, football:5, premier league:5, goals:4, shorts:6, tiktok:4, meme:4, memes:4, compilation:4, fail:3, prank:3, challenge:3, reaction:4, try not to laugh:2, music:5, mv:4, music video:4, cover:5, remix:4, lyrics:4, karaoke:3, kpop:5, jpop:4, hip hop:4, rap:5, edm:4, lo-fi:3, lofi:3, anime:5, vtuber:5, cosplay:3, trailer:5, official trailer:5, teaser:4, netflix:3, marvel:3, dc:2, vlog:4, daily vlog:3, travel:4, food:4, street food:3, cooking:5, recipe:5, mukbang:4, review:4, unboxing:5, tech review:5, iphone:5, samsung:4, smartphone:4, camera:4, gopro:3, drone:3, how to:5, tutorial:5, tips:3, tricks:3, diy:4, life hacks:3, education:3, science:4, math:3, physics:3, chemistry:2, coding:4, programming:4, python:4, javascript:3, ai:6, artificial intelligence:3, chatgpt:5, openai:4, stable diffusion:3, gen ai:3, podcast:4, interview:4, talk show:3, debate:2, study with me:3, pomodoro:2, finance:3, investing:3, stock market:4, crypto:4, bitcoin:4, ethereum:3, fitness:4, workout:4, gym:3, yoga:3, meditation:2, beauty:3, makeup:4, hairstyle:3, skincare:4, fashion:4, haul:3, pets:3, cat:4, dog:4, animals:3, kids:3, nursery rhymes:3, cartoons:3, documentary:3, history:3, space:4, nasa:3, rocket:3, launch:3, boxing:3, ufc:3, mma:3, cricket:4, cricket highlights:4, tennis:3, golf:2, motorsport:3, formula 1:4, f1:4, apex legends:3, cs2:3, counter strike:3, dota 2:3, rust game:2, ark:2, among us:3, speedrun:3, walkthrough:4, lets play:4, top 10:4"

# Region-specific pools (override global when region matches)
$env:YT_RANDOM_QUERY_POOL_US = "nfl:5, nba:4, mlb:3, highlights:5, live:3, trailer:2"
$env:YT_RANDOM_QUERY_POOL_JP = "vtuber:5, anime:4, jpop:3, showcase:2, live:3"
$env:YT_RANDOM_QUERY_POOL_VN = "nhac tre:3, rap viet:4, bong da:4, highlights:5, live:3"

# Tracker milestones for 24h viral modeling (optional override)
# Uncomment to test denser early sampling:
# $env:YT_TRACK_PLAN_MINUTES = "5,10,15,20,25,30,45,60,75,90,105,120,135,150,165,180,210,240,270,300,330,360,390,420,450,480,540,600,660,720,780,840,900,960,1020,1080,1140,1200,1260,1320,1380,1440"

# -------------------------------
# 🏃 Helper to run a step and capture exit code
# -------------------------------
function Run-Step([string]$Name, [string]$ScriptRelPath) {
    try {
        $full = Join-Path $WorkerDir $ScriptRelPath
        if (-not (Test-Path $full)) {
            Write-Log "Script not found: $full" "ERROR"
            return
        }
        Write-Log "Running $Name ($full)"
        & $PythonExe $full
        $code = $LASTEXITCODE
        Write-Log "$Name exit code = $code"
        if ($code -eq 88) {
            Write-Log "$Name detected YouTube quota exhausted (exit 88). Cooling down 15 minutes." "WARN"
            Start-Sleep -Seconds 900
        }
    } catch {
        Write-Log "Exception while running $Name: $($_.Exception.Message)" "ERROR"
    }
}

# -------------------------------
# 🚀 Main loop (staggered scheduler)
# -------------------------------
Write-Log "Starting combined runner (discover + tracker)."

$NextDiscover = Get-Date
$NextTrack    = Get-Date

while ($true) {
    $now = Get-Date

    if ($now -ge $NextDiscover) {
        Run-Step -Name "discover_once" -ScriptRelPath "discover_once.py"
        $NextDiscover = $now.AddSeconds($DiscoverIntervalSeconds)
    }

    if ($now -ge $NextTrack) {
        Run-Step -Name "track_once" -ScriptRelPath "track_once.py"
        $NextTrack = $now.AddSeconds($TrackIntervalSeconds)
    }

    Start-Sleep -Seconds $TickSleepSeconds
}
