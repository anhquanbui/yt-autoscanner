# Milestones test
$env:YT_TRACK_PLAN_MINUTES = "5,10,15,20,25,30"

# File log (please change this path if needed, this is my own path)
$logPath = "D:\PYTHON\PROJECT\yt-autoscanner\logs\track_log.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $logPath) | Out-Null

# Loop 30s interval
while ($true) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host ">>> Running tracker at $timestamp"
    ">>> Running tracker at $timestamp" | Out-File -FilePath $logPath -Append

    try {
        python .\worker\track_once.py 2>&1 | Tee-Object -FilePath $logPath -Append
    } catch {
        "Tracker error: $($_.Exception.Message)" | Out-File -FilePath $logPath -Append
    }

    Start-Sleep -Seconds 30
}
