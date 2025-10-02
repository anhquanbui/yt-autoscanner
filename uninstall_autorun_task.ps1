# uninstall_autorun_task.ps1
# Removes the Scheduled Task created by install_autorun_task.ps1
$task = "YT Discover Loop"
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $task -Confirm:$false
  Write-Host "Removed scheduled task '$task'." -ForegroundColor Yellow
} else {
  Write-Host "No scheduled task named '$task' found."
}
