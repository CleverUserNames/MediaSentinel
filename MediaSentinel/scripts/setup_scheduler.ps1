# ============================================================
# MediaSentinel - Task Scheduler Setup
# Run once as Administrator to configure automatic monitoring
# ============================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$LogDir    = Join-Path $RootDir "logs"

# Auto-detect Python executable
$PyExe = (Get-Command python  -ErrorAction SilentlyContinue)?.Source
if (-not $PyExe) { $PyExe = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source }
if (-not $PyExe) {
    Write-Error "Python not found in PATH. Install Python 3.9+ and ensure it is on the system PATH."
    exit 1
}

Write-Host "Setting up MediaSentinel scheduled tasks..." -ForegroundColor Cyan

# -- Task 1: Full monitor every 15 minutes -----------------------------
Write-Host "Creating 15-minute monitor task..." -ForegroundColor Cyan

$action1 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptDir\run_monitor.ps1`"" `
    -WorkingDirectory $ScriptDir

# Trigger: every 15 minutes, starting now, repeating indefinitely
$trigger1 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) -Once -At (Get-Date)

$settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

$principal1 = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName "MediaSentinel Monitor" `
    -TaskPath "\MediaSentinel\" `
    -Action $action1 `
    -Trigger $trigger1 `
    -Settings $settings1 `
    -Principal $principal1 `
    -Description "Collects media server data and generates the monitoring report every 15 minutes" `
    -Force | Out-Null

Write-Host "    OK 15-minute monitor task created" -ForegroundColor Green

# -- Task 2: Daily run with recommendations at 6am --------------------
Write-Host "Creating daily recommendations task..." -ForegroundColor Cyan

$action2 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptDir\run_monitor.ps1`" -WithRecommendations" `
    -WorkingDirectory $ScriptDir

$trigger2 = New-ScheduledTaskTrigger -Daily -At "06:00AM"

$settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName "MediaSentinel Daily Recommendations" `
    -TaskPath "\MediaSentinel\" `
    -Action $action2 `
    -Trigger $trigger2 `
    -Settings $settings2 `
    -Principal $principal1 `
    -Description "Daily full run including recommendations refresh at 6am" `
    -Force | Out-Null

Write-Host "    OK Daily recommendations task created" -ForegroundColor Green

# -- Task 3: Weekly transcode report (Sunday 6:30am) ------------------
Write-Host "Creating weekly transcode report task..." -ForegroundColor Cyan

$action_weekly = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"& '$PyExe' -W ignore '$ScriptDir\weekly_transcode_report.py' 2>> '$LogDir\scheduler.log'`"" `
    -WorkingDirectory $ScriptDir

$trigger_weekly = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Sunday -At "06:30AM"

$settings_weekly = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "MediaSentinel Weekly Transcode Report" `
    -TaskPath "\MediaSentinel" `
    -Action $action_weekly `
    -Trigger $trigger_weekly `
    -Settings $settings_weekly `
    -Principal $principal1 `
    -Description "Generates weekly transcode analysis report every Sunday at 6:30am" `
    -Force | Out-Null

Write-Host "    OK Weekly transcode report task created" -ForegroundColor Green

# -- Task 4: HTTP server for report serving ----------------------------
Write-Host "Creating HTTP server task..." -ForegroundColor Cyan

$action3 = New-ScheduledTaskAction `
    -Execute $PyExe `
    -Argument "-m http.server 8765 --directory `"$LogDir`"" `
    -WorkingDirectory $LogDir

$trigger3 = New-ScheduledTaskTrigger -AtStartup

$settings3 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "MediaSentinel HTTP Server" `
    -TaskPath "\MediaSentinel\" `
    -Action $action3 `
    -Trigger $trigger3 `
    -Settings $settings3 `
    -Principal $principal1 `
    -Description "Serves MediaSentinel reports over HTTP on port 8765" `
    -Force | Out-Null

Write-Host "    OK HTTP server task created" -ForegroundColor Green

# -- Start tasks now ---------------------------------------------------
Write-Host ""
Write-Host "Starting tasks..." -ForegroundColor Cyan

Start-ScheduledTask -TaskPath "\MediaSentinel\" -TaskName "MediaSentinel HTTP Server"
Write-Host "    OK HTTP server started" -ForegroundColor Green

Start-ScheduledTask -TaskPath "\MediaSentinel\" -TaskName "MediaSentinel Monitor"
Write-Host "    OK First monitor run started" -ForegroundColor Green

# -- Verify ------------------------------------------------------------
Write-Host ""
Write-Host "Scheduled tasks registered:" -ForegroundColor Cyan
Get-ScheduledTask -TaskPath "\MediaSentinel\" | Select-Object TaskName, State | Format-Table -AutoSize

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Tasks created:" -ForegroundColor White
Write-Host "  - Monitor runs every 15 minutes" -ForegroundColor White
Write-Host "  - Recommendations refresh daily at 6am" -ForegroundColor White
Write-Host "  - HTTP server starts on boot (port 8765)" -ForegroundColor White
Write-Host ""
Write-Host "  Manage tasks in Task Scheduler under \MediaSentinel\" -ForegroundColor White
Write-Host "  Logs at: $LogDir\scheduler.log" -ForegroundColor White
Write-Host "============================================" -ForegroundColor Cyan
