# ============================================================
# MediaSentinel Monitor - Windows PowerShell Orchestrator
# Usage:
#   .\monitor.ps1              -> collect + analyze, print report
#   .\monitor.ps1 -Fix         -> dry-run automated fixes
#   .\monitor.ps1 -Fix -Execute -> apply fixes for real
#   .\monitor.ps1 -Report      -> also generate HTML report
#   .\monitor.ps1 -Watch       -> run continuously on schedule
# ============================================================

param(
    [switch]$Fix,
    [switch]$Execute,
    [switch]$Report,
    [switch]$Watch
)

# Auto-detect Python executable
$PythonExe = (Get-Command python  -ErrorAction SilentlyContinue)?.Source
if (-not $PythonExe) { $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source }
if (-not $PythonExe) {
    Write-Error "Python not found in PATH. Install Python 3.9+ and ensure it is on the system PATH."
    exit 1
}
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir    = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $RootDir "logs"
$ConfigFile = Join-Path $RootDir "config\config.json"
$Timestamp  = (Get-Date -Format "yyyyMMddTHHmmss")
$DataFile     = Join-Path $LogDir "data_$Timestamp.json"
$AnalysisFile = Join-Path $LogDir "analysis_$Timestamp.json"
$FixFile      = Join-Path $LogDir "fixes_$Timestamp.json"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log($msg) {
    [Console]::WriteLine($msg)
}

function RunOnce {
    $serverIp = ""
    try {
        $cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        $serverIp = $cfg.server.ip
    } catch {}
    Log ""
    Log "============================================"
    Log "  MediaSentinel v1.0"
    if ($serverIp) { Log "  Server: $serverIp" }
    Log "  $(Get-Date -Format 'yyyy-MM-dd HH:mm') UTC"
    Log "============================================"
    Log ""

    # ── Step 1: Collect data ─────────────────────────────────────────
    Log "[1/3] Collecting data from media stack..."
    $collectScript = Join-Path $ScriptDir "collect_data.py"

    try {
        $rawData = & $PythonExe -W ignore $collectScript 2>$null
        if (-not $rawData) { throw "collect_data.py returned no output" }
        [System.IO.File]::WriteAllText($DataFile, ($rawData -join "`n"), [System.Text.UTF8Encoding]::new($false))
        Log "    OK Data saved to $DataFile"
    } catch {
        Log "    FAIL Could not collect data: $_"
        return
    }

    # Quick stats summary
    $summaryScript = @"
import json, sys
try:
    d = json.load(open(r'$DataFile', encoding='utf-8'))
    s = next((x for x in d['services'] if x['service']=='sonarr'), {})
    r = next((x for x in d['services'] if x['service']=='radarr'), {})
    print(str(len(s.get('missing_episodes',[]))) + '|' + str(len(r.get('missing_movies',[]))) + '|0')
except Exception as e:
    print('?|?|?')
"@
    $summary = & $PythonExe -c $summaryScript 2>$null
    $parts   = $summary -split "\|"
    Log "    -> Missing episodes: $($parts[0])  |  Missing movies: $($parts[1])  |  Active transcodes: $($parts[2])"

    # ── Step 2: AI Analysis ──────────────────────────────────────────
    Log ""
    Log "[2/3] Sending data to LM Studio for analysis..."
    $analyzeScript = Join-Path $ScriptDir "analyze.py"

    try {
        $analysisRaw = & $PythonExe -W ignore $analyzeScript $DataFile 2>$null
        if (-not $analysisRaw) { throw "analyze.py returned no output" }
        [System.IO.File]::WriteAllText($AnalysisFile, ($analysisRaw -join "`n"), [System.Text.UTF8Encoding]::new($false))
        Log "    OK Analysis saved to $AnalysisFile"
    } catch {
        Log "    FAIL Analysis failed: $_"
        return
    }

    # Print analysis summary
    $printScript = @"
import json, sys
try:
    a = json.load(open(r'$AnalysisFile', encoding='utf-8'))
except Exception as e:
    print('Could not parse analysis: ' + str(e))
    sys.exit(0)
if 'error' in a:
    print('ERROR: ' + str(a['error']))
    sys.exit(0)
score   = a.get('health_score', '?')
summary = a.get('summary', 'No summary available')
marker  = 'GOOD' if isinstance(score,int) and score >= 80 else 'WARN' if isinstance(score,int) and score >= 60 else 'BAD'
print('[' + marker + '] Health Score: ' + str(score) + '/100')
print('Status: ' + summary)
print('')
issues = a.get('issues', [])
by_sev = {'CRITICAL': [], 'WARNING': [], 'INFO': []}
for i in issues:
    by_sev.get(i.get('severity','INFO'), by_sev['INFO']).append(i)
if by_sev['CRITICAL']:
    print('CRITICAL (' + str(len(by_sev['CRITICAL'])) + '):')
    for i in by_sev['CRITICAL']:
        print('  [!] [' + i['service'] + '] ' + i['title'])
if by_sev['WARNING']:
    print('WARNING (' + str(len(by_sev['WARNING'])) + '):')
    for i in by_sev['WARNING']:
        print('  [W] [' + i['service'] + '] ' + i['title'])
if by_sev['INFO']:
    print('INFO (' + str(len(by_sev['INFO'])) + '):')
    for i in by_sev['INFO'][:5]:
        print('  [i] [' + i['service'] + '] ' + i['title'])
ta = a.get('transcode_analysis', {})
if ta:
    print('')
    print('Transcode: ' + str(ta.get('active_count',0)) + ' active | HW: ' + str(ta.get('hw_acceleration_in_use')) + ' | Risk: ' + str(ta.get('bottleneck_risk','?')))
for s in a.get('proactive_suggestions', [])[:3]:
    print('>> ' + s)
print('')
"@
    $result = & $PythonExe -c $printScript 2>$null
    foreach ($line in $result) { Log $line }

    # ── Step 3: Fix executor ─────────────────────────────────────────
    if ($Fix) {
        $fixScript = Join-Path $RootDir "tools\fix_executor.py"
        if ($Execute) {
            Log "[3/3] Applying automated fixes (LIVE MODE)..."
            & $PythonExe $fixScript --execute --analysis $AnalysisFile | Out-File -FilePath $FixFile -Encoding utf8
        } else {
            Log "[3/3] Simulating automated fixes (dry-run)..."
            & $PythonExe $fixScript --analysis $AnalysisFile | Out-File -FilePath $FixFile -Encoding utf8
        }
        Log "    OK Fix results saved to $FixFile"
        $fixPrint = @"
import json
results = json.load(open(r'$FixFile', encoding='utf-8'))
for r in results:
    s  = 'OK'    if r.get('success') else 'FAIL'
    dr = ' [DRY]' if r.get('dry_run') else ''
    er = ' - ' + r['error'] if r.get('error') else ''
    print('[' + s + '] ' + r['action'] + dr + er)
"@
        $fixResult = & $PythonExe -c $fixPrint 2>$null
        foreach ($line in $fixResult) { Log $line }
    }

    # ── HTML Report ──────────────────────────────────────────────────
    if ($Report) {
        Log ""
        Log "Generating HTML report..."
        $reportScript = Join-Path $ScriptDir "render_report.py"
        $reportFile   = Join-Path $LogDir "report_$Timestamp.html"
        & $PythonExe $reportScript $AnalysisFile $DataFile | Out-File -FilePath $reportFile -Encoding utf8
        Log "    OK Report saved to $reportFile"
    }

    Log ""
    Log "Done. Logs in $LogDir"
    Log ""
}

# ── Entry point ───────────────────────────────────────────────────────
if ($Watch) {
    $cfg      = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    $interval = $cfg.schedule.monitor_interval_minutes
    Log "Watch mode: running every $interval minutes. Ctrl+C to stop."
    while ($true) {
        RunOnce
        Log "Next check in $interval minutes..."
        Start-Sleep -Seconds ($interval * 60)
    }
} else {
    RunOnce
}
