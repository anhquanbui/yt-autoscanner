
<#
  run_discover_loop.ps1 (v4)
  - Default ProjectRoot = folder of this script
  - Single-instance lock to prevent duplicate loops
  - cd to ProjectRoot so python-dotenv can load .env
  - Optional -ApiKey; if not set, rely on .env
  - UTF-8 console
  - Logs to logs/discover-YYYYMMDD.log via StreamWriter (FileShare.ReadWrite)
  - Starts worker\scheduler.py (loop that invokes discover_once.py)
#>

param(
  [string]$ProjectRoot = $PSScriptRoot,
  [int]$IntervalSeconds = 30,
  [string]$ApiKey,
  # Optional overrides (only applied if provided)
  [bool]$RandomMode,
  [int]$RandomLookbackMinutes,
  [int]$RandomWindowMinutes,
  [string]$RandomRegionPool,
  [string]$RandomQueryPool,
  [string]$Region,
  [int]$LookbackMinutes,
  [string]$Query,
  [string]$ChannelId,
  [string]$ChannelHandle,
  [string]$TopicId,
  [string]$CategoryId,
  [string]$MongoUri
)

# --- UTF-8 console / Python ---
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# --- Validate & cd ---
if (-not (Test-Path $ProjectRoot)) {
  Write-Error "ProjectRoot not found: $ProjectRoot"
  exit 1
}
Set-Location $ProjectRoot

Write-Host "ProjectRoot = $ProjectRoot" -ForegroundColor Cyan
Write-Host "IntervalSeconds = $IntervalSeconds" -ForegroundColor Cyan

# --- Single-instance lock ---
$lockPath = Join-Path $ProjectRoot ".run.lock"
try {
  $global:lockStream = [System.IO.File]::Open(
    $lockPath,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
} catch {
  Write-Error "Another instance is already running ($lockPath)."
  exit 1
}

# --- Ensure cleanup on exit ---
$script:cleanupDone = $false
function Cleanup {
  if ($script:cleanupDone) { return }
  $script:cleanupDone = $true
  try { if ($global:sw) { $global:sw.Dispose() } } catch {}
  try { if ($global:fs) { $global:fs.Dispose() } } catch {}
  try {
    if (Test-Path $lockPath) {
      $global:lockStream.Close()
      Remove-Item $lockPath -ErrorAction SilentlyContinue
    }
  } catch {}
}

# Trap Ctrl+C and script end
$OnCancel = {
  Write-Host "Stopping..." -ForegroundColor Yellow
  Cleanup
}
Register-EngineEvent PowerShell.Exiting -Action $OnCancel | Out-Null

# --- Activate venv if present ---
$venvActivate = Join-Path $ProjectRoot "venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
  Write-Host "Activating venv..." -ForegroundColor DarkCyan
  . $venvActivate
} else {
  Write-Host "venv not found, using system Python." -ForegroundColor Yellow
}

# --- Apply environment variables if provided ---
if ($PSBoundParameters.ContainsKey('IntervalSeconds')) { $env:DISCOVER_INTERVAL_SECONDS = "$IntervalSeconds" }
if ($PSBoundParameters.ContainsKey('ApiKey') -and $ApiKey) { $env:YT_API_KEY = $ApiKey }
if ($PSBoundParameters.ContainsKey('MongoUri') -and $MongoUri) { $env:MONGO_URI = $MongoUri }

if ($PSBoundParameters.ContainsKey('RandomMode')) { $env:YT_RANDOM_MODE = $(if($RandomMode){'1'}else{'0'}) }
if ($PSBoundParameters.ContainsKey('RandomLookbackMinutes') -and $RandomLookbackMinutes) { $env:YT_RANDOM_LOOKBACK_MINUTES = "$RandomLookbackMinutes" }
if ($PSBoundParameters.ContainsKey('RandomWindowMinutes') -and $RandomWindowMinutes) { $env:YT_RANDOM_WINDOW_MINUTES = "$RandomWindowMinutes" }
if ($PSBoundParameters.ContainsKey('RandomRegionPool') -and $RandomRegionPool) { $env:YT_RANDOM_REGION_POOL = $RandomRegionPool }
if ($PSBoundParameters.ContainsKey('RandomQueryPool') -and $RandomQueryPool) { $env:YT_RANDOM_QUERY_POOL = $RandomQueryPool }
if ($PSBoundParameters.ContainsKey('Region') -and $Region) { $env:YT_REGION = $Region }
if ($PSBoundParameters.ContainsKey('LookbackMinutes') -and $LookbackMinutes) { $env:YT_LOOKBACK_MINUTES = "$LookbackMinutes" }
if ($PSBoundParameters.ContainsKey('Query') -and $Query) { $env:YT_QUERY = $Query }
if ($PSBoundParameters.ContainsKey('ChannelId') -and $ChannelId) { $env:YT_CHANNEL_ID = $ChannelId }
if ($PSBoundParameters.ContainsKey('ChannelHandle') -and $ChannelHandle) { $env:YT_CHANNEL_HANDLE = $ChannelHandle }
if ($PSBoundParameters.ContainsKey('TopicId') -and $TopicId) { $env:YT_TOPIC_ID = $TopicId }
if ($PSBoundParameters.ContainsKey('CategoryId') -and $CategoryId) { $env:YT_FILTER_CATEGORY_ID = $CategoryId }

if (-not $env:YT_API_KEY) {
  Write-Host "YT_API_KEY not set via env/param. Relying on .env (python-dotenv) if present." -ForegroundColor Yellow
}

# --- Logs (safe writer) ---
$logs = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }
$logFile = Join-Path $logs ("discover-{0}.log" -f (Get-Date -Format yyyyMMdd))
Write-Host "Log file: $logFile" -ForegroundColor DarkCyan

$global:fs = [System.IO.File]::Open($logFile,
  [System.IO.FileMode]::Append,
  [System.IO.FileAccess]::Write,
  [System.IO.FileShare]::ReadWrite)
$utf8bom = New-Object System.Text.UTF8Encoding($true)
$global:sw = New-Object System.IO.StreamWriter($fs, $utf8bom)
$global:sw.AutoFlush = $true

function Write-Log([string]$line) {
  Write-Output $line
  $global:sw.WriteLine($line)
}

# --- Check scheduler exists ---
$scriptPath = Join-Path $ProjectRoot "worker\scheduler.py"
if (-not (Test-Path $scriptPath)) {
  Write-Error "Cannot find $scriptPath. Ensure worker\scheduler.py exists."
  Cleanup
  exit 1
}

Write-Log ("[{0}] === RUN START ===" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Write-Host "Starting scheduler (interval=$IntervalSeconds s). Press Ctrl+C to stop." -ForegroundColor Cyan

# --- Start child process (unbuffered) ---
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "python"
$psi.Arguments = "-u `"$scriptPath`""
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$proc.Start() | Out-Null

$stdOut = $proc.StandardOutput
$stdErr = $proc.StandardError

try {
  while (-not $proc.HasExited) {
    while (-not $stdOut.EndOfStream) { Write-Log ($stdOut.ReadLine()) }
    while (-not $stdErr.EndOfStream) { Write-Log ($stdErr.ReadLine()) }
    Start-Sleep -Milliseconds 100
  }
  while (-not $stdOut.EndOfStream) { Write-Log ($stdOut.ReadLine()) }
  while (-not $stdErr.EndOfStream) { Write-Log ($stdErr.ReadLine()) }
} finally {
  $exitCode = $proc.ExitCode
  Write-Log ("[{0}] === RUN END (code={1}) ===" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $exitCode)
  Cleanup
}

exit $exitCode