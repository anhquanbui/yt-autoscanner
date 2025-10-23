
<# 
  run_both_local.ps1 (v5.1) — Local runner for discover_once.py + track_once.py
  Adds weighted random pick for YouTube video length buckets (short/medium/long/any).
  PowerShell 5 compatible (no '??' operator).
#>

# =======================
# 🔁 Intervals (seconds)
# =======================
$DiscoverIntervalSeconds = 300   # 30 minutes between discover runs
$TrackIntervalSeconds    = 15    # 30 seconds between track runs
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
        if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
        $parts = $line -split '=', 2
        if ($parts.Count -lt 2) { continue }
        $k = $parts[0].Trim()
        $v = $parts[1].Trim()
        if ($v.StartsWith('"') -and $v.EndsWith('"')) { $v = $v.Substring(1, $v.Length-2) }
        elseif ($v.StartsWith("'") -and $v.EndsWith("'")) { $v = $v.Substring(1, $v.Length-2) }
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
if (-not $env:YT_SINCE_MINUTES) { $env:YT_SINCE_MINUTES = "10" }
if (-not $env:YT_MAX_PAGES)     { $env:YT_MAX_PAGES     = "3"  }

# Random region + weighted keywords
if (-not $env:YT_RANDOM_PICK)        { $env:YT_RANDOM_PICK        = "1" }
if (-not $env:YT_RANDOM_REGION_POOL) { $env:YT_RANDOM_REGION_POOL = "US,GB,CA,AU,IN,JP,VN,KR,FR,DE,BR,MX,ID,TH,ES,IT,NL,SG,MY,PH,TW,HK,AR,CL,TR,PL,SA,AE,EG,NG,KE,RU,SE,NO,FI,DK,IE,PT,GR,IL,ZA" }
if (-not $env:YT_RANDOM_QUERY_POOL)  {
  # --- Expanded random QUERY pool (balanced across categories) ---
$env:YT_RANDOM_QUERY_POOL  = "live:6, breaking news:5, world news:5, update:3, politics:3, president speech:2, economy:3, stock market:4, crypto:4, bitcoin:4, ethereum:3, finance:3, investing:3, business:3, startup:3, " +
                             "ai:6, artificial intelligence:4, chatgpt:5, openai:4, gen ai:3, machine learning:3, tech review:5, iphone:5, samsung:4, smartphone:4, camera:4, unboxing:5, gadgets:4, drone:3, gopro:3, pc build:3, linux:2, " +
                             "coding:4, programming:4, python:4, javascript:3, sql:2, how to:5, tutorial:5, diy:4, life hacks:3, tips:3, tricks:3, education:4, science:4, math:3, physics:3, chemistry:2, " +
                             "space:4, nasa:3, rocket launch:3, astronomy:3, documentary:3, history:3, geography:2, nature:3, animals:3, wildlife:3, pets:3, cat:4, dog:4, zoo:2, " +
                             "gaming:6, esports:5, fortnite:5, minecraft:5, roblox:4, valorant:5, league of legends:5, genshin impact:4, pubg:3, mobile legends:3, fifa:4, nba:4, soccer:5, football:5, cricket:4, boxing:3, ufc:3, mma:3, " +
                             "motorsport:3, formula 1:4, f1:4, racing:3, car review:3, gta 6:5, apex legends:3, cs2:3, counter strike:3, dota 2:3, rust:2, ark:2, among us:3, speedrun:3, walkthrough:4, lets play:4, top 10:4, " +
                             "shorts:6, meme:4, memes:4, funny moments:3, fail:3, prank:3, challenge:3, try not to laugh:2, reaction:4, compilation:4, " +
                             "music:5, mv:4, music video:4, cover:5, remix:4, lyrics:4, karaoke:3, kpop:5, jpop:4, hip hop:4, rap:5, edm:4, lofi:3, classical music:2, jazz:2, " +
                             "podcast:4, interview:4, talk show:3, debate:2, stand up comedy:3, " +
                             "vlog:4, daily vlog:3, travel vlog:4, travel:4, street food:4, cooking:5, recipe:5, mukbang:4, review:4, unboxing:5, restaurant:3, cafe:3, coffee:3, tech review:5, " +
                             "iphone:5, samsung:4, smartphone:4, camera:4, gopro:3, drone:3, " +
                             "beauty:3, makeup:4, hairstyle:3, skincare:4, fashion:4, ootd:3, haul:3, " +
                             "fitness:4, workout:4, gym:3, yoga:3, meditation:2, health:3, doctor:2, " +
                             "kids:3, baby:3, parenting:2, nursery rhymes:3, cartoons:3, " +
                             "anime:5, vtuber:5, cosplay:3, manga:3, fan animation:3, trailer:5, official trailer:5, teaser:4, netflix:3, marvel:3, dc:2, " +
                             "study with me:3, pomodoro:2, productivity:3, motivation:3, self improvement:3, education tips:3, career advice:2"
}

# =======================
# 🎚️ Duration weighting (NEW)
# =======================
if (-not $env:YT_DURATION_POOL) { $env:YT_DURATION_POOL = "short:1,medium:3,long:3,any:0" }

function Parse-WeightedPool([string]$Pool) {
    $pairs = @()
    foreach ($raw in ($Pool -split ',')) {
        $s = $raw.Trim()
        if (-not $s) { continue }
        if ($s -like "*:*") {
            $spl = $s.Split(':',2)
            $name = $spl[0].Trim()
            $wstr = $spl[1].Trim()
            try { $weight = [double]::Parse($wstr) } catch { $weight = 1.0 }
        } else {
            $name = $s
            $weight = 1.0
        }
        if ($name -and ($weight -gt 0)) {
            $pairs += [PSCustomObject]@{ Name=$name; Weight=$weight }
        }
    }
    return $pairs
}

function Pick-ByWeight([object[]]$Pairs) {
    if (-not $Pairs -or $Pairs.Count -eq 0) { return $null }
    $totalObj = $Pairs | Measure-Object -Property Weight -Sum
    $total = [double]$totalObj.Sum
    if ($total -le 0) { return $Pairs[0].Name }
    $r = Get-Random -Minimum 0.0 -Maximum $total
    $acc = 0.0
    foreach ($p in $Pairs) {
        $acc = $acc + [double]$p.Weight
        if ($r -le $acc) { return $p.Name }
    }
    return $Pairs[$Pairs.Count-1].Name
}

function Pick-DurationBucket() {
    # Respect static setting if user forces a bucket via env
    $mode = $env:YT_DURATION_MODE
    if (-not $mode) { $mode = "mix" }
    $mode = $mode.ToLower()
    if ($mode -in @("short","medium","long","any")) { return $mode }
    # Otherwise pick by weights each discover run
    $pairs = Parse-WeightedPool $env:YT_DURATION_POOL
    $pick = Pick-ByWeight $pairs
    if (-not $pick) { return "any" }
    return $pick
}

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
        if ($Name -eq "discover_once") {
            $bucket = Pick-DurationBucket
            $env:YT_DURATION_MODE = $bucket
            Write-Log ("Duration bucket this run: {0}" -f $bucket)
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
