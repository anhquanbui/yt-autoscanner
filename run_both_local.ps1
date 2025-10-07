<# 
  run_both_local.ps1 — Local runner for discover_once.py + track_once.py
  - Auto-loads .env (YT_API_KEY, MONGO_URI, etc.) without hardcoding secrets
  - Sets runtime configs (near-now discover + random region/keywords)
  - Runs discover & tracker on a staggered loop
  - Logs to console and to logs/scanner-YYYY-MM-DD.log
#>

# =======================
# 🔁 Intervals (seconds)
# =======================
$DiscoverIntervalSeconds = 1800   # 30 minutes between discover runs
$TrackIntervalSeconds    = 30     # 30 seconds between track runs
$TickSleepSeconds        = 5      # main loop tick sleep

# =======================
# 🛤️ Paths
# =======================
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = $ScriptDir              # repo root (this ps1 sits at repo root)
$WorkerDir = Join-Path $RepoRoot "worker"
$PythonExe = "python"                # or "py"

# =======================
# 🧾 Logging
# =======================
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
try {
    $TranscriptPath = Join-Path $LogDir ("transcript-" + (Get-Date -Format "yyyy-MM-dd") + ".log")
    if (-not (Test-Path $TranscriptPath)) { New-Item -ItemType File -Path $TranscriptPath | Out-Null }
    Start-Transcript -Path $TranscriptPath -Append | Out-Null
} catch { Write-Log "Start-Transcript failed: $($_.Exception.Message)" "WARN" }

# =======================
# 📦 Load .env (NO hardcoding)
# =======================
function Load-DotEnv([string]$FilePath) {
    if (-not (Test-Path $FilePath)) { return }
    $lines = Get-Content -Raw -Path $FilePath -Encoding UTF8 -ErrorAction SilentlyContinue -ReadCount 0
    foreach ($line in ($lines -split "`r?`n")) {
        # Skip comments/blank
        if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
        # Split on first '='
        $parts = $line -split '=', 2
        if ($parts.Count -lt 2) { continue }
        $k = $parts[0].Trim()
        $v = $parts[1].Trim()
        # Remove surrounding quotes if present
        if ($v.StartsWith('"') -and $v.EndsWith('"')) { $v = $v.Substring(1, $v.Length-2) }
        elseif ($v.StartsWith("'") -and $v.EndsWith("'")) { $v = $v.Substring(1, $v.Length-2) }
        # Assign to process environment
        ${env:$k} = $v
    }
}

$DotEnvPath = Join-Path $RepoRoot ".env"
Load-DotEnv -FilePath $DotEnvPath

# Masked log for key state (no secrets printed)
if ($env:YT_API_KEY) {
    $head = $env:YT_API_KEY.Substring(0, [Math]::Min(6, $env:YT_API_KEY.Length))
    $tail = $env:YT_API_KEY.Substring([Math]::Max(0, $env:YT_API_KEY.Length-4))
    Write-Log ("YT_API_KEY loaded from .env (masked): {0}...{1}" -f $head, $tail)
} else {
    Write-Log "YT_API_KEY is empty. Please put it in .env (YT_API_KEY=...)" "WARN"
}
if ($env:MONGO_URI) {
    Write-Log "MONGO_URI loaded from .env"
} else {
    Write-Log "MONGO_URI is empty. Please put it in .env (MONGO_URI=mongodb://...)" "WARN"
}

# =======================
# 🌐 Runtime config (non-secret)
# =======================
# Keep existing env if already set; otherwise apply sensible defaults.
if (-not $env:YT_SINCE_MINUTES) { $env:YT_SINCE_MINUTES = "20" }
if (-not $env:YT_MAX_PAGES)     { $env:YT_MAX_PAGES     = "1"  }

# Random region + weighted keywords
if (-not $env:YT_RANDOM_PICK)        { $env:YT_RANDOM_PICK        = "1" }
if (-not $env:YT_RANDOM_REGION_POOL) { $env:YT_RANDOM_REGION_POOL = "US,GB,CA,AU,IN,JP,VN,KR,FR,DE,BR,MX,ID,TH,ES,IT" }
if (-not $env:YT_RANDOM_QUERY_POOL)  {
  $env:YT_RANDOM_QUERY_POOL  = "live:6, breaking news:5, news:5, update:3, " +
                               "gaming:6, highlights:6, esports:4, fortnite:5, minecraft:5, roblox:4, valorant:5, league of legends:5, genshin impact:4, pubg:3, mobile legends:3, fifa:4, nba:4, nfl:4, soccer:5, football:5, premier league:5, goals:4, " +
                               "shorts:6, tiktok:4, meme:4, memes:4, compilation:4, fail:3, prank:3, challenge:3, reaction:4, try not to laugh:2, " +
                               "music:5, mv:4, music video:4, cover:5, remix:4, lyrics:4, karaoke:3, kpop:5, jpop:4, hip hop:4, rap:5, edm:4, lo-fi:3, lofi:3, " +
                               "anime:5, vtuber:5, cosplay:3, trailer:5, official trailer:5, teaser:4, netflix:3, marvel:3, dc:2, " +
                               "vlog:4, daily vlog:3, travel:4, food:4, street food:3, cooking:5, recipe:5, mukbang:4, review:4, unboxing:5, tech review:5, " +
                               "iphone:5, samsung:4, smartphone:4, camera:4, gopro:3, drone:3, " +
                               "how to:5, tutorial:5, tips:3, tricks:3, diy:4, life hacks:3, " +
                               "education:3, science:4, math:3, physics:3, chemistry:2, " +
                               "coding:4, programming:4, python:4, javascript:3, ai:6, artificial intelligence:3, chatgpt:5, openai:4, stable diffusion:3, gen ai:3, " +
                               "podcast:4, interview:4, talk show:3, debate:2, " +
                               "study with me:3, pomodoro:2, " +
                               "finance:3, investing:3, stock market:4, crypto:4, bitcoin:4, ethereum:3, " +
                               "fitness:4, workout:4, gym:3, yoga:3, meditation:2, " +
                               "beauty:3, makeup:4, hairstyle:3, skincare:4, fashion:4, haul:3, " +
                               "pets:3, cat:4, dog:4, animals:3, " +
                               "kids:3, nursery rhymes:3, cartoons:3, " +
                               "documentary:3, history:3, space:4, nasa:3, rocket:3, launch:3, " +
                               "boxing:3, ufc:3, mma:3, cricket:4, cricket highlights:4, tennis:3, golf:2, motorsport:3, formula 1:4, f1:4, " +
                               "apex legends:3, cs2:3, counter strike:3, dota 2:3, rust game:2, ark:2, among us:3, speedrun:3, walkthrough:4, lets play:4, top 10:4"
}
if (-not $env:YT_RANDOM_QUERY_POOL_US) { $env:YT_RANDOM_QUERY_POOL_US = "nfl:5, nba:4, mlb:3, highlights:5, live:3, trailer:2" }
if (-not $env:YT_RANDOM_QUERY_POOL_JP) { $env:YT_RANDOM_QUERY_POOL_JP = "vtuber:5, anime:4, jpop:3, showcase:2, live:3" }
if (-not $env:YT_RANDOM_QUERY_POOL_VN) { $env:YT_RANDOM_QUERY_POOL_VN = "nhac tre:3, rap viet:4, bong da:4, highlights:5, live:3" }

# =======================
# 🏃 Helper to run a step
# =======================
function Run-Step([string]$Name, [string]$ScriptRelPath) {
    try {
        $full = Join-Path $WorkerDir $ScriptRelPath
        if (-not (Test-Path $full)) {
            Write-Log "Script not found: $full" "ERROR"
            return
        }
        Write-Log "Running $Name ($full)"
        Push-Location $RepoRoot  # ensure Python sees .env at repo root
        & $PythonExe $full
        $code = $LASTEXITCODE
        Pop-Location
        Write-Log "$Name exit code = $code"
        if ($code -eq 88) {
            Write-Log "$Name detected YouTube quota exhausted (exit 88). Cooling down 15 minutes." "WARN"
            Start-Sleep -Seconds 900
        }
    } catch {
        Write-Log "Exception while running ${Name}: $($_.Exception.Message)" "ERROR"
    }
}

# =======================
# 🚀 Main loop
# =======================
Write-Log "Starting combined runner (discover + tracker). Intervals: discover=$DiscoverIntervalSeconds s, track=$TrackIntervalSeconds s"

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

# End (Transcript stops when PS session ends)
