# ============================================================
# MediaSentinel - Scheduled Task Runner
# This script is called by Task Scheduler
# ============================================================

param(
    [switch]$WithRecommendations
)

# Auto-detect Python executable
$py = (Get-Command python  -ErrorAction SilentlyContinue)?.Source
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source }
if (-not $py) {
    [Console]::Error.WriteLine("ERROR: Python not found in PATH. Install Python 3.9+ and ensure it is on the system PATH.")
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$logs      = Join-Path $RootDir "logs"
$ts   = (Get-Date -Format "yyyyMMddTHHmmss")
$utf8 = [System.Text.UTF8Encoding]::new($false)

$data     = "$logs\data_$ts.json"
$analysis = "$logs\analysis_$ts.json"

# Log file for scheduled runs
$logFile = "$logs\scheduler.log"
function Write-Log($msg) {
    $entry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $entry
    [Console]::WriteLine($entry)
}

Write-Log "=== MediaSentinel run started (WithRecommendations=$WithRecommendations) ==="

# Step 1: SMART health (requires admin)
Write-Log "Collecting SMART data..."
try {
    [System.IO.File]::WriteAllText(
        "$logs\smart_cache.json",
        (& $py -W ignore $ScriptDir\collect_smart.py 2>$null) -join "`n",
        $utf8
    )
    Write-Log "SMART data collected"
} catch {
    Write-Log "SMART collection failed: $_"
}

# Step 2: Collect data
Write-Log "Collecting service data..."
try {
    [System.IO.File]::WriteAllText(
        $data,
        (& $py -W ignore $ScriptDir\collect_data.py 2>$null) -join "`n",
        $utf8
    )
    Write-Log "Service data collected"
} catch {
    Write-Log "Data collection failed: $_"
    exit 1
}

# Step 3: AI Analysis
Write-Log "Running AI analysis..."
try {
    [System.IO.File]::WriteAllText(
        $analysis,
        (& $py -W ignore $ScriptDir\analyze.py $data 2>$null) -join "`n",
        $utf8
    )
    Write-Log "Analysis complete"
} catch {
    Write-Log "Analysis failed: $_"
    exit 1
}

# Step 4: Log transcodes to database
Write-Log "Logging transcode history..."
try {
    $logResult = & $py -W ignore $ScriptDir\log_transcodes.py $data 2>&1
    Write-Log "Transcode log: $logResult"
} catch {
    Write-Log "Transcode logging failed: $_"
}

# Step 4: Generate main report
Write-Log "Generating HTML report..."
try {
    [System.IO.File]::WriteAllText(
        "$logs\report_latest.html",
        (& $py -W ignore $ScriptDir\render_report.py $analysis $data 2>$null) -join "`n",
        $utf8
    )
    Write-Log "Report saved to $logs\report_latest.html"
} catch {
    Write-Log "Report generation failed: $_"
}

# Step 5: Recommendations (only on daily run)
if ($WithRecommendations) {
    Write-Log "Generating recommendations..."
    try {
        [System.IO.File]::WriteAllText(
            "$logs\recommendations.html",
            (& $py -W ignore $ScriptDir\render_recommendations_page.py $data 2>$null) -join "`n",
            $utf8
        )
        Write-Log "Recommendations saved"
    } catch {
        Write-Log "Recommendations failed: $_"
    }
}

# Step 6: Clean up old log files (keep last 48)
try {
    Get-ChildItem $logs -Filter "data_*.json" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 48 |
        Remove-Item -Force
    Get-ChildItem $logs -Filter "analysis_*.json" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 48 |
        Remove-Item -Force
} catch {}

Write-Log "=== Run complete ==="
