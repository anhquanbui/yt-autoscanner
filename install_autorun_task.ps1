<#
 install_autorun_task.ps1
 ------------------------
 Registers a Windows Scheduled Task to auto-start the discover loop on user logon.
 EDIT the parameters below to match your setup.

 USAGE:
   .\install_autorun_task.ps1
   # Later, to remove:
   # Unregister-ScheduledTask -TaskName "YT Discover Loop" -Confirm:$false
#>

# ===== CHANGE ME: set where your project lives =====
$ProjectRoot = "D:\PROJECT\yt-autoscanner"

# ===== CHANGE ME: which script to run and interval =====
$RunnerScript = Join-Path $ProjectRoot "run_discover_loop.ps1"
$IntervalSeconds = 30

# ===== OPTIONAL: Random Mode toggle (true/false) =====
$RandomMode = $true

# Build PowerShell arguments (quote carefully)
$randFlag = if ($RandomMode) { "-RandomMode `$true" } else { "-RandomMode `$false" }
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunnerScript`" -ProjectRoot `"$ProjectRoot`" -IntervalSeconds $IntervalSeconds $randFlag"

$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -Action $action -Trigger $trigger -TaskName "YT Discover Loop" -Description "Run yt-autoscanner discover loop" -Settings $settings -User $env:UserName
Write-Host "Scheduled Task 'YT Discover Loop' installed. It will start on logon." -ForegroundColor Green
