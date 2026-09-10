# ============================================================
# MediaSentinel Setup - Windows PowerShell
# Run this ONCE before using monitor.ps1
# Usage: .\setup.ps1
# ============================================================

# Auto-detect Python and pip executables
$PythonExe = (Get-Command python  -ErrorAction SilentlyContinue)?.Source
if (-not $PythonExe) { $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source }
$PipExe    = (Get-Command pip     -ErrorAction SilentlyContinue)?.Source
if (-not $PipExe)    { $PipExe    = (Get-Command pip3    -ErrorAction SilentlyContinue)?.Source }
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir    = Split-Path -Parent $ScriptDir
$ConfigFile = Join-Path $RootDir "config\config.json"
$LogDir     = Join-Path $RootDir "logs"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  MediaSentinel Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$AllGood = $true

# ── Step 1: Python ───────────────────────────────────────────────────
Write-Host "[1/5] Checking Python..." -ForegroundColor Cyan
if ($PythonExe -and (Test-Path $PythonExe)) {
    $pyVersion = & $PythonExe --version 2>&1
    Write-Host "    OK $pyVersion found at $PythonExe" -ForegroundColor Green
} else {
    Write-Host "    FAIL Python not found in PATH." -ForegroundColor Red
    Write-Host "         Install Python 3.9+ from https://python.org and ensure" -ForegroundColor Yellow
    Write-Host "         'Add Python to PATH' is checked during installation." -ForegroundColor Yellow
    $AllGood = $false
}

# ── Step 2: Python packages ──────────────────────────────────────────
Write-Host ""
Write-Host "[2/5] Installing Python packages (requests, psutil)..." -ForegroundColor Cyan
if ($PipExe -and (Test-Path $PipExe)) {
    & $PipExe install requests psutil --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    OK requests and psutil installed" -ForegroundColor Green
    } else {
        Write-Host "    FAIL pip install failed" -ForegroundColor Red
        $AllGood = $false
    }
} elseif ($PythonExe) {
    # Fallback: use python -m pip
    & $PythonExe -m pip install requests psutil --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    OK requests and psutil installed (via python -m pip)" -ForegroundColor Green
    } else {
        Write-Host "    FAIL pip install failed" -ForegroundColor Red
        $AllGood = $false
    }
} else {
    Write-Host "    FAIL pip not found — install Python first" -ForegroundColor Red
    $AllGood = $false
}

# ── Step 3: Config file ──────────────────────────────────────────────
Write-Host ""
Write-Host "[3/5] Checking config.json..." -ForegroundColor Cyan
if (-not (Test-Path $ConfigFile)) {
    Write-Host "    FAIL config.json not found at $ConfigFile" -ForegroundColor Red
    $AllGood = $false
} else {
    try {
        $cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json

        $placeholders = @()
        $services = @("jellyfin","sonarr","radarr","jellyseer","tautulli")
        foreach ($svc in $services) {
            $key = $cfg.services.$svc.api_key
            if ($key -like "YOUR_*" -or [string]::IsNullOrWhiteSpace($key)) {
                $placeholders += $svc
            }
        }

        if ($placeholders.Count -gt 0) {
            Write-Host "    WARN These API keys are still placeholders:" -ForegroundColor Yellow
            foreach ($p in $placeholders) {
                Write-Host "         - $p" -ForegroundColor Yellow
            }
            Write-Host "         Edit config\config.json and fill in your real API keys." -ForegroundColor Yellow
        } else {
            Write-Host "    OK All API keys are set" -ForegroundColor Green
        }

        $lmHost  = $cfg.server.local_ai_host
        $lmModel = $cfg.server.local_ai_model
        if ($lmHost -like "*YOUR_LMSTUDIO*" -or [string]::IsNullOrWhiteSpace($lmHost)) {
            Write-Host "    WARN LM Studio host is not set in config.json" -ForegroundColor Yellow
            $AllGood = $false
        } else {
            Write-Host "    OK LM Studio host: $lmHost" -ForegroundColor Green
            Write-Host "    OK Model: $lmModel" -ForegroundColor Green
        }
    } catch {
        Write-Host "    FAIL config.json could not be parsed: $_" -ForegroundColor Red
        $AllGood = $false
    }
}

# ── Step 4: LM Studio connectivity ──────────────────────────────────
Write-Host ""
Write-Host "[4/5] Testing LM Studio connectivity..." -ForegroundColor Cyan
try {
    $cfg    = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    $lmHost = $cfg.server.local_ai_host

    if ($lmHost -like "*YOUR_LMSTUDIO*" -or [string]::IsNullOrWhiteSpace($lmHost)) {
        Write-Host "    SKIP LM Studio host not configured yet" -ForegroundColor Yellow
    } else {
        $response = Invoke-RestMethod -Uri "$lmHost/v1/models" -TimeoutSec 6 -ErrorAction Stop
        $modelIds = $response.data | ForEach-Object { $_.id }
        Write-Host "    OK LM Studio reachable at $lmHost" -ForegroundColor Green
        foreach ($m in $modelIds) {
            Write-Host "       - $m" -ForegroundColor Green
        }
        $configModel = $cfg.server.local_ai_model
        if ($modelIds -notcontains $configModel) {
            Write-Host "    WARN Model '$configModel' is not loaded in LM Studio." -ForegroundColor Yellow
            Write-Host "         Load it in LM Studio or update local_ai_model in config.json" -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "    FAIL Cannot reach LM Studio: $_" -ForegroundColor Red
    Write-Host "         - Confirm LM Studio server is running" -ForegroundColor Yellow
    Write-Host "         - Confirm 'Serve on Local Network' is enabled" -ForegroundColor Yellow
    Write-Host "         - Confirm firewall allows port 1234" -ForegroundColor Yellow
    $AllGood = $false
}

# ── Step 5: Verify all files present ────────────────────────────────
Write-Host ""
Write-Host "[5/5] Checking all required files..." -ForegroundColor Cyan
$requiredFiles = @(
    "config\config.json",
    "scripts\collect_data.py",
    "scripts\analyze.py",
    "scripts\monitor.ps1",
    "scripts\render_report.py",
    "tools\fix_executor.py"
)
$missingFiles = @()
foreach ($f in $requiredFiles) {
    $full = Join-Path $RootDir $f
    if (-not (Test-Path $full)) { $missingFiles += $f }
}
if ($missingFiles.Count -gt 0) {
    Write-Host "    FAIL Missing files:" -ForegroundColor Red
    foreach ($f in $missingFiles) {
        Write-Host "         - $f" -ForegroundColor Red
    }
    $AllGood = $false
} else {
    Write-Host "    OK All required files present" -ForegroundColor Green
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Host "    OK Logs folder ready at $LogDir" -ForegroundColor Green

# ── Summary ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
if ($AllGood) {
    Write-Host "  Setup complete! Everything looks good." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Run the monitor:" -ForegroundColor White
    Write-Host "    cd $ScriptDir" -ForegroundColor White
    Write-Host "    .\monitor.ps1              # run a check now" -ForegroundColor White
    Write-Host "    .\monitor.ps1 -Report      # generate HTML report" -ForegroundColor White
    Write-Host "    .\monitor.ps1 -Watch       # run every 15 minutes" -ForegroundColor White
} else {
    Write-Host "  Setup finished with warnings/errors." -ForegroundColor Yellow
    Write-Host "  Fix the items marked FAIL or WARN above" -ForegroundColor Yellow
    Write-Host "  then run .\setup.ps1 again." -ForegroundColor Yellow
}
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""