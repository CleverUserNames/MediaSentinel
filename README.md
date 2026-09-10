# MediaSentinel

This is my first project using any sort of AI, local or cloud. I have used Plex for years, but recently got fed up with playback issues and paywalls so I switched to Jellyfin and this was built to help me identify issues. Please be gentle. 

Self-hosted monitoring dashboard for a Jellyfin media server stack. Collects data from Jellyfin, Sonarr, Radarr, and Jellyseer every 15 minutes, analyzes it with a local AI model, and generates an HTML report with health scores, active transcodes, drive health, playback errors, import failures, and content recommendations.

---

## Features

- **Health score & issue cards** — CRITICAL / WARNING / INFO issues with fix steps
- **Active transcode monitor** — source vs. stream target, why it's transcoding, fix options
- **Playback error detection** — parses Jellyfin and FFmpeg logs for crash loops, broken MKV keyframes, missing HLS segments
- **Import failure detection** — Sonarr/Radarr downloads that completed but never imported
- **Jellyseer request tracking** — pending, approved, failed, and processing requests
- **Drive health (SMART)** — per-drive cards with temperature, power-on hours, reallocated sectors
- **AI-powered recommendations** — based on watch history + TMDB, displayed as a card grid with direct Jellyseer request links
- **Weekly transcode report** — frequency table, transcode reasons, codec breakdown, Tdarr/FFmpeg fix suggestions
- **Android TV app** — dedicated NVIDIA Shield app (see `MediaSentinelTV/`)

---

## Requirements

- Windows Server (or Windows 10/11)
- Python 3.9+ on the system PATH
- [LM Studio](https://lmstudio.ai/) running a local model on another machine (or the same one)
- [smartmontools](https://www.smartmontools.org/) installed for drive health monitoring
- Jellyfin, Sonarr, Radarr, Jellyseer running and accessible on your LAN
- A free [TMDB API](https://developer.themoviedb.org/docs) read token for recommendations

---

## Quick Start

### 1. Clone the repo

```powershell
git clone https://github.com/CleverUserNames/MediaSentinel.git C:\MediaSentinel
cd C:\MediaSentinel
```
OR Download the zip file and extract to the C: Drive. Rename the unzipped folder "MediaSentinel".
### 2. Configure

```powershell
copy config\config.example.json config\config.json
notepad config\config.json
```

Fill in your API keys, service URLs, LM Studio host, and drive paths. See [Configuration](#configuration) below for details.

### 3. Run setup

```powershell
# Open PowerShell as Administrator
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\scripts\setup.ps1
```

This checks Python, installs dependencies, validates your config, and tests LM Studio connectivity.

### 4. Set up scheduled tasks

```powershell
# Open PowerShell as Administrator
.\scripts\setup_scheduler.ps1
```

This registers four Task Scheduler tasks:
- Monitor every 15 minutes
- Daily recommendations refresh at 6:00 AM
- Weekly transcode report every Sunday at 6:30 AM
- HTTP server on port 8765 (starts at boot)

### 5. View the report

Open `http://YOUR_SERVER_IP:8765/report_latest.html` in any browser on your network.

---

## Configuration

Copy `config/config.example.json` to `config/config.json` and fill in your values. `config.json` is excluded from the repo via `.gitignore` — your keys will never be committed.

### Required fields

| Field | Description |
|-------|-------------|
| `server.ip` | Your media server's LAN IP |
| `server.local_ai_host` | LM Studio URL, e.g. `http://192.168.1.101:1234` |
| `server.local_ai_model` | Model identifier as shown in LM Studio |
| `services.jellyfin.url` + `api_key` | Jellyfin server URL and API key |
| `services.sonarr.url` + `api_key` | Sonarr URL and API key |
| `services.radarr.url` + `api_key` | Radarr URL and API key |
| `services.jellyseer.url` + `api_key` | Jellyseer URL and API key |
| `services.tmdb.api_read_token` | TMDB API v4 read access token |
| `thresholds.monitored_drives` | Drive letters to monitor, e.g. `["K:\\"]` |
| `thresholds.drivepool_drive` | Primary pool drive letter |
| `recommendations.jellyfin_users` | Array of `{name, id}` — get user IDs from Jellyfin Dashboard > Users |

### Optional path overrides

All paths below have sensible defaults. Only set them if your installation differs.

| Field | Default |
|-------|---------|
| `paths.jellyfin_transcode` | `C:\ProgramData\Jellyfin\Server\transcodes` |
| `paths.jellyfin_log_dir` | `C:\ProgramData\Jellyfin\Server\log` |
| `paths.smartctl` | `C:\Program Files\smartmontools\bin\smartctl.exe` |
| `paths.system_drive` | `C:\` |
| `paths.smart_drives` | `[/dev/sda–sdd (ata), /dev/sde (nvme)]` |

---

## Manual run

To run a full cycle manually without waiting for the scheduler:

```powershell
cd C:\MediaSentinel\scripts
.\monitor.ps1              # collect + analyze, print summary
.\monitor.ps1 -Report      # also generate the HTML report
.\monitor.ps1 -Fix         # dry-run automated fixes
.\monitor.ps1 -Fix -Execute  # apply fixes for real
.\monitor.ps1 -Watch       # run continuously every 15 min
```

---

## File structure

```
MediaSentinel\
├── config\
│   ├── config.example.json      ← template — copy and fill in
│   └── config.json              ← your config (git-ignored)
├── scripts\
│   ├── collect_data.py          ← polls all services
│   ├── collect_smart.py         ← SMART drive health (run as Admin)
│   ├── collect_recommendations.py
│   ├── collect_import_failures.py
│   ├── collect_playback_errors.py
│   ├── analyze.py               ← sends data to LM Studio
│   ├── render_report.py         ← generates report_latest.html
│   ├── render_recommendations_page.py
│   ├── render_import_failures.py
│   ├── render_playback_errors.py
│   ├── log_transcodes.py        ← logs transcodes to SQLite
│   ├── weekly_transcode_report.py
│   ├── monitor.ps1              ← interactive CLI runner
│   ├── run_monitor.ps1          ← called by Task Scheduler
│   ├── setup.ps1                ← one-time setup checker
│   └── setup_scheduler.ps1     ← registers Task Scheduler tasks
├── tools\
│   └── fix_executor.py          ← applies automated fixes
├── prompts\
│   ├── system_analyst.txt       ← AI system prompt
│   └── transcode_analyst.txt
└── logs\                        ← generated at runtime (git-ignored)
    ├── report_latest.html
    ├── recommendations.html
    ├── weekly_transcode_latest.html
    ├── transcodes.db
    └── ...
```

---

## Notes

- **SMART collection requires Administrator.** The Task Scheduler tasks run as SYSTEM which has the required rights. Manual runs need an elevated PowerShell prompt.
- **LM Studio must be running** with "Serve on Local Network" enabled and a model loaded before the monitor runs.
- **Python must be on the system PATH** — check "Add Python to PATH" during the Python installer.
- See `MEDIASENTINEL_DOCS.md` for full technical documentation, troubleshooting, and architecture details.

---

## License

MIT
