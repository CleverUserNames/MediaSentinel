# MediaSentinel Documentation

**Version:** 1.0  
**Platform:** Windows Server  
**Stack:** Jellyfin, Sonarr, Radarr, Jellyseer, LM Studio, Tdarr

---

## Table of Contents

1. [What is MediaSentinel](#what-is-mediasentinel)
2. [Architecture Overview](#architecture-overview)
3. [File Structure](#file-structure)
4. [Report Sections Explained](#report-sections-explained)
5. [Playback Error Detection](#playback-error-detection)
6. [Import Failure Detection](#import-failure-detection)
7. [Transcode History](#transcode-history)
8. [Recommendation Engine](#recommendation-engine)
9. [Drive Health Monitoring](#drive-health-monitoring)
10. [Download Pipeline](#download-pipeline)
11. [Tdarr GPU Encoding Setup](#tdarr-gpu-encoding-setup)
12. [Task Scheduler](#task-scheduler)
13. [One-Liners and Useful Commands](#one-liners-and-useful-commands)
14. [Troubleshooting](#troubleshooting)
15. [Known Limitations](#known-limitations)
16. [Technical Decisions and Lessons Learned](#technical-decisions-and-lessons-learned)

---

## What is MediaSentinel

MediaSentinel is a self-hosted media server monitoring system that runs alongside a Jellyfin media stack. It collects data from all services every 15 minutes, analyzes it using a local AI model, and generates an HTML dashboard report that can be viewed in a browser, embedded in Jellyfin as a custom tab, or viewed on an Android TV device via a dedicated app.

**What it monitors:**
- Active Jellyfin playback sessions and transcodes
- Sonarr and Radarr download queue and health
- Jellyseer content requests and library status
- Drive health via SMART data
- Playback errors and crash loops from Jellyfin and FFmpeg logs
- Import failures  -  downloads that completed but were never imported into the library
- Content recommendations based on watch history

**What it does not do:**
- Replace Sonarr/Radarr/Jellyfin  -  it reads from them, never writes except for triggering scans
- Require any cloud services  -  everything runs locally
- Require an internet connection except for TMDB API calls for recommendations

---

## Architecture Overview

```
Task Scheduler (every 15 min)
        |
        v
run_monitor.ps1
        |
        +-- collect_smart.py        -> smart_cache.json
        |
        +-- collect_data.py         -> data_TIMESTAMP.json
        |       |
        |       +-- collect_import_failures.py   (Sonarr/Radarr API)
        |       +-- collect_playback_errors.py   (Jellyfin log files)
        |       +-- collect_recommendations.py   (Jellyfin + TMDB APIs)
        |
        +-- analyze.py              -> analysis_TIMESTAMP.json
        |       |
        |       +-- LM Studio API (local AI)
        |
        +-- render_report.py        -> report_latest.html
        |       |
        |       +-- render_import_failures.py
        |       +-- render_playback_errors.py
        |
        +-- render_recommendations_page.py -> recommendations.html
        |
        +-- log_transcodes.py       -> transcodes.db (SQLite)
        |
        HTTP server (:8765)         serves HTML files
```

**Data flow:** Each run produces timestamped JSON files. The report always reads the latest. Old data files accumulate in the logs folder and can be deleted periodically.

---

## File Structure

```
MediaSentinel/
+-- config/
|   +-- config.json              <- API keys, thresholds, service URLs
+-- scripts/
|   +-- collect_data.py          <- Main data collector, calls sub-collectors
|   +-- collect_smart.py         <- SMART drive health (requires Admin)
|   +-- collect_recommendations.py <- Watch history + TMDB + AI ranking
|   +-- collect_import_failures.py <- Sonarr/Radarr stuck import detector
|   +-- collect_playback_errors.py <- Jellyfin log parser
|   +-- analyze.py               <- Sends data to local AI for analysis
|   +-- render_report.py         <- Builds main HTML report
|   +-- render_import_failures.py <- Import failures HTML section
|   +-- render_playback_errors.py <- Playback errors HTML section
|   +-- render_recommendations_page.py <- Standalone recommendations page
|   +-- log_transcodes.py        <- Writes active transcodes to SQLite
|   +-- weekly_transcode_report.py <- Weekly transcode analysis
|   +-- run_monitor.ps1          <- Master script called by Task Scheduler
|   +-- setup_scheduler.ps1      <- One-time Task Scheduler setup
+-- tools/
|   +-- fix_executor.py          <- Automated fixes (dry-run by default)
+-- prompts/
|   +-- system_analyst.txt       <- Main AI system prompt
|   +-- transcode_analyst.txt    <- Transcode AI prompt
+-- logs/
    +-- report_latest.html       <- Current report (served on :8765)
    +-- recommendations.html     <- Recommendations page
    +-- weekly_transcode_latest.html
    +-- transcodes.db            <- SQLite transcode history
    +-- smart_cache.json         <- SMART data from last run
    +-- tmdb_cache.json          <- TMDB API response cache
    +-- scheduler.log            <- Task Scheduler execution log
    +-- data_*.json              <- Per-run raw data
    +-- analysis_*.json          <- Per-run AI analysis
```

---

## config.json Structure

```json
{
  "server": {
    "ip": "YOUR_SERVER_IP",
    "local_ai_host": "http://YOUR_AI_HOST:1234",
    "local_ai_model": "your-model-name",
    "ai_backend": "lmstudio"
  },
  "services": {
    "jellyfin":  { "url": "http://localhost:8096",  "api_key": "YOUR_KEY" },
    "sonarr":    { "url": "http://localhost:8989",  "api_key": "YOUR_KEY" },
    "radarr":    { "url": "http://localhost:7878",  "api_key": "YOUR_KEY" },
    "jellyseer": { "url": "http://localhost:5055",  "api_key": "YOUR_KEY" },
    "tmdb":      { "api_read_token": "YOUR_TOKEN" }
  },
  "paths": {
    "jellyfin_transcode": "PATH_TO_JELLYFIN_TRANSCODES"
  },
  "thresholds": {
    "disk_free_good_gb": 2000,
    "disk_free_warning_gb": 1500,
    "disk_free_critical_gb": 1000,
    "monitored_drives": ["K:\\"],
    "drivepool_drive": "K:\\"
  },
  "recommendations": {
    "jellyfin_users": [
      {"name": "Username", "id": "JELLYFIN_USER_ID"}
    ],
    "history_limit": 30,
    "suggestions_per_item": 3,
    "max_suggestions": 20,
    "min_tmdb_rating": 6.5
  }
}
```

---

## Report Sections Explained

The main report at `http://localhost:8765/report_latest.html` auto-refreshes every 15 minutes. Here is what each section means and how to interpret it.

### Header - Health Score

A number from 0-100 representing overall stack health. Calculated by the AI based on active issues, transcode load, drive health, and service errors.

- **80-100** - All systems healthy
- **60-79** - Minor issues present, worth investigating
- **Below 60** - Active problems requiring attention

### Stats Row

Six counters across the top:
- **Critical Issues** - Issues requiring immediate action
- **Warnings** - Issues to monitor
- **Info** - Informational notices
- **Active Transcodes** - Streams currently being transcoded by Jellyfin
- **Stuck Imports** - Downloads completed but not imported into library
- **Playback Errors** - Incidents detected in Jellyfin logs in last 24 hours

### Issues Section

AI-generated issue cards ranked by severity (CRITICAL > WARNING > INFO). Each card contains:
- **Service** - Which service the issue relates to (Jellyfin, Sonarr, Radarr, etc.)
- **Category** - Type of issue (disk, transcode, health, metadata, etc.)
- **Description** - What the problem is
- **Fix steps** - Numbered steps to resolve it
- **Fix type** - Whether the fix is manual, automated, or informational

### Playback Errors Section

Incidents detected by parsing Jellyfin server logs and FFmpeg session logs from the last 24 hours. See [Playback Error Detection](#playback-error-detection) for full details.

### Active Transcodes Section

Shows any streams currently being transcoded. Each card displays:

- **User and client** - Who is watching and on what device
- **Source file specs** - The actual file's video codec, resolution, bitrate, HDR type, audio codec and channels
- **Streaming as** - What Jellyfin is sending to the client (target codec, resolution, bitrate)
- **Why transcoding** - Human-readable explanation of each transcode reason
- **Options to eliminate transcode** - Specific steps to allow direct play for this file/client combination

**Common transcode reasons:**
- `AudioCodecNotSupported` - Client cannot play the source audio codec (e.g. DTS-X on Roku)
- `VideoBitrateNotSupported` - File bitrate exceeds client profile limit
- `VideoRangeTypeNotSupported` - HDR format not supported by client profile (e.g. Dolby Vision on H264 profile)
- `VideoCodecNotSupported` - Client cannot play source video codec

### Import Failures Section

Downloads that completed in the download client but were never imported into Sonarr or Radarr's media library. See [Import Failure Detection](#import-failure-detection) for full details.

### Jellyseer - Requested Items Not In Library

All items requested via Jellyseer that have not yet appeared in the Jellyfin library. Statuses:

- **PENDING** - Request submitted, waiting for approval
- **APPROVED** - Approved, waiting for download
- **PROCESSING** - Being downloaded
- **FAILED** - Download or import failed

### Recommended - Not In Library

AI-curated content recommendations based on watch history. Shows poster, rating, genres, and why the item was recommended. Each card has a "Request in Jellyseer" button that deep-links to the Jellyseer request page.

### Drive Health (SMART)

Per-drive health cards showing:
- **Model and serial** - Drive identification
- **Health status** - OK / WARNING / CRITICAL based on SMART attributes
- **Temperature** - Current drive temperature in Celsius
- **Power-on hours** - Total lifetime hours (7.6 years = ~66,000 hours)
- **Key SMART attributes** - Reallocated sectors, pending sectors, uncorrectable errors, CRC errors
- **Issues list** - Specific problems detected

**What to watch for:**
- Reallocated sectors > 0 = drive has bad sectors, monitor closely
- Pending sectors > 0 = sectors waiting to be reallocated, imminent failure risk
- High CRC errors = SATA cable issue, not drive failure
- Temperature above 55C = cooling problem
- Power-on hours above 50,000 = consider replacement planning

### Proactive Suggestions

AI-generated recommendations for improving the stack based on current data. Not necessarily urgent issues but things worth addressing.

---

## Playback Error Detection

`collect_playback_errors.py` runs every 15 minutes as part of the main collection cycle. It parses two log sources:

- Jellyfin server log: `C:\ProgramData\Jellyfin\Server\log\log_YYYYMMDD.log`
- FFmpeg session logs: `C:\ProgramData\Jellyfin\Server\log\FFmpeg.Transcode-*.log`

### Incident Types

**Matroska Keyframe Failure (CRITICAL)**

The MKV file has a corrupt or missing seek table. Jellyfin cannot build a chapter index for HLS streaming. Causes FFmpeg to crash when the client seeks or requests segments.

*Symptom:* Playback stops when seeking, or video freezes and restarts repeatedly.

*Fix:* Remux the file to rebuild the container without re-encoding:
```
ffmpeg -i "broken_file.mkv" -c copy "fixed_file.mkv"
```
Replace the original after verifying the fixed file plays correctly. Alternatively, re-encoding the file to HEVC via Tdarr will also fix the container as a side effect.

**FFmpeg Crash - Exit Code -1 (CRITICAL)**

FFmpeg terminated abnormally. Almost always paired with Matroska keyframe failures or disk space issues.

*Fix:* Check the FFmpeg session log for the same timestamp. Look for the specific error line above the exit code line. Common causes: corrupt input file, codec unsupported by installed FFmpeg build, transcode cache full.

**Missing HLS Segment (WARNING)**

The client requested a `.ts` segment file that no longer exists in the transcode cache. Jellyfin's cache cleanup deleted it before the client fetched it.

*Symptom:* Playback stutters or jumps backward during continuous viewing.

*Fix:* Check available space on the drive where the transcode cache lives. Jellyfin aggressively clears cache when space is low. Consider moving the transcode cache to a larger drive or increasing the cache size limit in Jellyfin Dashboard > Playback.

**HLS Segment Cancel Burst (CRITICAL when 5+ in under 2 minutes)**

The client canceled multiple segment requests in rapid succession. In small numbers this is normal seeking behavior. In bursts it indicates the client is in a restart loop after a playback failure.

*Symptom:* Video repeatedly jumps back to an earlier position, or shows a spinner and restarts.

*Fix:* Look for a paired FFmpeg crash or missing segment error at the same timestamp. The cancellations are a symptom, not the cause.

**OMDB Metadata Error (INFO)**

The Open Movie Database API returned invalid JSON during a background metadata refresh. Does not affect playback at all.

*Fix:* No action needed. If persistent, disable OMDB as a metadata provider in Jellyfin Dashboard > Libraries > (library) > Metadata.

### How Events Are Grouped

Multiple errors on the same file within a time window are grouped into a single incident card with an occurrence count. Time windows by error type:

- Matroska keyframe failures: 10 minute window
- Task cancellations: 2 minute window
- Missing segments: 2 minute window
- FFmpeg crashes: 1 minute window
- OMDB errors: 24 hour window (entire day collapsed into one note)

### FFmpeg Session Stats

The report shows two counters:
- **FFmpeg sessions (24h)** - Total number of FFmpeg transcode sessions started
- **Crash loops (<15s)** - Sessions that lasted under 15 seconds, indicating a failed start

A healthy system has sessions lasting the full duration of what was watched. Multiple very short sessions for the same media item indicates a crash loop.

---

## Import Failure Detection

`collect_import_failures.py` checks both Sonarr and Radarr every 15 minutes.

### Two Data Sources

**Queue** (`/api/v3/queue`)

Downloads that are complete at the download client level but stuck in Sonarr/Radarr with a failed import state. Key states:

- `importPending` - Arr is attempting import but hasn't succeeded
- `importFailed` - Arr attempted import and gave up

**History** (`/api/v3/history`)

Items that fell out of the queue in the last 48 hours without a successful import. These are downloads that completed and disappeared from the queue but never showed up in the library.

### Grace Period

Items less than 30 minutes old are ignored. This prevents false positives on fresh downloads that haven't had time for the arr to attempt import yet. Adjustable in the script via `GRACE_PERIOD_MINUTES`.

### Common Failure Reasons and Fixes

**"No files found are eligible for import"**

The folder name doesn't match what the arr expects. The arr parses folder names to identify the series or movie. If the folder was named differently than the arr's naming convention, it can't make the match.

*Fix:* In Sonarr or Radarr, go to Activity > Queue, click the item, and use Manual Import to browse to the file and assign it manually.

**"Folder name mismatch"**

Explicit naming conflict between the download folder name and what the arr expects.

*Fix:* Manual Import as above, or rename the folder to match the arr's expected format and trigger a rescan.

**"Found matching series via grab history, but..."**

The arr found the series in its grab history but path resolution failed.

*Fix:* Manual Import. The arr knows what the file is, it just can't find it automatically.

**"No space left"**

Destination drive has no available space.

*Fix:* Free up space on the media drive before attempting import again.

### Root Cause - Why Most Import Failures Happen

Files downloaded via a remote seedbox and transferred via FTP arrive in the watched folder with no grab history in Sonarr/Radarr. The arr never issued the download command, so it has no metadata to match the incoming file against. It must parse the folder name cold, which fails when naming doesn't exactly match the arr's conventions.

**The fix:** After transferring files to the watched folder, trigger an arr scan via API:

```powershell
# Sonarr
$body = @{ name = "DownloadedEpisodesScan"; path = "PATH_TO_FINISHED_FOLDER"; importMode = "Move" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8989/api/v3/command" -Method Post -Headers @{ "X-Api-Key" = "YOUR_KEY" } -ContentType "application/json" -Body $body

# Radarr
$body = @{ name = "DownloadedMoviesScan"; path = "PATH_TO_FINISHED_FOLDER"; importMode = "Move" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:7878/api/v3/command" -Method Post -Headers @{ "X-Api-Key" = "YOUR_KEY" } -ContentType "application/json" -Body $body
```

This tells the arr to scan the folder and do its best to match what it finds using its full parsing engine, which is more forgiving than the cold folder-name check.

---

## Transcode History

`log_transcodes.py` writes to `logs/transcodes.db` (SQLite) after every collection run.

### Database Structure

**Table: `transcode_sessions`** (not `transcodes`)

Stores one record per unique title+user+client combination per day. Retains 180 days of history.

Fields captured: title, user, client, date, source video codec, source audio codec, source resolution, HDR type, destination codec, transcode reasons, hardware acceleration type.

### Querying the Database

Always use a temp file approach for queries due to PowerShell quote escaping issues with inline Python:

```powershell
$py = "PATH_TO_PYTHON_EXE"

# Check what tables exist
@"
import sqlite3
conn = sqlite3.connect(r'PATH_TO_MEDIASENTINEL\logs\transcodes.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('Tables:', tables)
conn.close()
"@ | Out-File -FilePath "PATH_TO_MEDIASENTINEL\logs\query.py" -Encoding utf8
& $py "PATH_TO_MEDIASENTINEL\logs\query.py"

# Return all records
@"
import sqlite3
conn = sqlite3.connect(r'PATH_TO_MEDIASENTINEL\logs\transcodes.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT * FROM transcode_sessions ORDER BY date DESC').fetchall()
for r in rows:
    print(dict(r))
conn.close()
"@ | Out-File -FilePath "PATH_TO_MEDIASENTINEL\logs\query.py" -Encoding utf8
& $py "PATH_TO_MEDIASENTINEL\logs\query.py"

# Most transcoded titles (last 30 days)
@"
import sqlite3
conn = sqlite3.connect(r'PATH_TO_MEDIASENTINEL\logs\transcodes.db')
rows = conn.execute("SELECT title, COUNT(*) as cnt FROM transcode_sessions WHERE date >= date('now', '-30 days') GROUP BY title ORDER BY cnt DESC LIMIT 20").fetchall()
for r in rows:
    print(f'{r[1]:>4}x  {r[0]}')
conn.close()
"@ | Out-File -FilePath "PATH_TO_MEDIASENTINEL\logs\query.py" -Encoding utf8
& $py "PATH_TO_MEDIASENTINEL\logs\query.py"

# Transcode reasons breakdown
@"
import sqlite3
conn = sqlite3.connect(r'PATH_TO_MEDIASENTINEL\logs\transcodes.db')
rows = conn.execute("SELECT transcode_reasons, COUNT(*) as cnt FROM transcode_sessions GROUP BY transcode_reasons ORDER BY cnt DESC").fetchall()
for r in rows:
    print(f'{r[1]:>4}x  {r[0]}')
conn.close()
"@ | Out-File -FilePath "PATH_TO_MEDIASENTINEL\logs\query.py" -Encoding utf8
& $py "PATH_TO_MEDIASENTINEL\logs\query.py"
```

### Note on Empty Database

The database will be empty until someone is actively transcoding when the collector runs. Health check runs and direct play sessions do not create records. Only active Jellyfin transcode sessions are logged.

### Weekly Transcode Report

`weekly_transcode_report.py` runs every Sunday at 6:30am. It reads 7 days of transcode history, sends the patterns to the local AI, and generates `logs/weekly_transcode_latest.html` containing:

- Top transcoded titles frequency table
- Transcode reasons breakdown
- Source codec analysis
- AI-generated issue cards with Tdarr plugin recommendations and FFmpeg fix commands
- Tdarr priority table

To run manually:
```powershell
& "PATH_TO_PYTHON" "PATH_TO_MEDIASENTINEL\scripts\weekly_transcode_report.py"
```

---

## Recommendation Engine

`collect_recommendations.py` runs daily at 6:00am via Task Scheduler.

### Data Sources

1. **Jellyfin watch history** - All unique series and movies watched (IsPlayed flag + partial watches over 20 minutes)
2. **TMDB similar/recommended** - For each watched item, fetches similar titles
3. **TMDB genre discover** - Top genres by watch frequency, 2021+ content only
4. **TMDB actor discover** - Actors appearing across multiple watched shows, 2018+ content
5. **AI ranking** - Sends numbered candidate list to local AI, AI returns numbers with reasons

### Exclusions

- Items already in the Jellyfin library
- Items already requested in Jellyseer
- Content older than 2019 (item-based), 2021 (genre discover), 2018 (actor discover)
- Items below minimum TMDB rating (default 6.5, configurable in config.json)

### Year Filters - Why They Exist

Without year filters, TMDB returns highly-rated older classics (Breaking Bad, The Wire, The Sopranos) for almost every query. The year cutoffs ensure recommendations are recent content the user likely hasn't seen rather than well-known classics.

### AI Hallucination Prevention

The AI is sent a numbered list of candidates and asked to return only `{"num": N, "reason": "..."}` for each selection. It is never asked to generate title names or metadata, only to rank existing items by number. This prevents the AI from inventing titles that don't exist.

### Performance

Approximately 13 seconds per run using parallel TMDB API calls via ThreadPoolExecutor with 8-15 workers. TMDB responses are cached in `logs/tmdb_cache.json` between runs.

### Library Scan Note

Must use direct `requests.Session()` with URL override for the Jellyfin library scan, not the `jf_get()` helper function. The helper function has a URL encoding bug that causes `HasTmdbId=true` to be silently dropped, returning only a subset of library items. This would cause already-owned items to appear as recommendations.

---

## Drive Health Monitoring

`collect_smart.py` reads SMART data from physical drives using smartmontools.

### Requirements

- smartmontools must be installed
- The script must be run as Administrator for full SMART data access
- In Task Scheduler, the task must run with highest privileges

### What Is Checked

- Overall SMART health assessment (PASSED/FAILED)
- Temperature
- Power-on hours (age indicator)
- Reallocated sectors (bad sector count)
- Pending sectors (sectors about to fail)
- Offline uncorrectable sectors
- UDMA CRC errors (cable/connection quality)
- Read/write error rates

### Severity Levels

- **OK** - No issues detected
- **INFO** - Worth noting but not urgent (e.g. drive age)
- **WARNING** - Monitor closely, plan action (e.g. reallocated sectors present)
- **CRITICAL** - Immediate action required (e.g. pending sectors, SMART test failed)

### Important Notes on UDMA CRC Errors

High CRC error counts (100+) indicate a problem with the SATA cable or connection, not the drive itself. Replace the cable before assuming drive failure. CRC errors that are stable (not increasing) are less concerning than errors that grow with each check.

### Data Flow

`collect_smart.py` writes to `logs/smart_cache.json`. `collect_data.py` reads from this cache rather than calling smartmontools directly, so SMART data is available even if the main collection runs without admin privileges.

---

## Download Pipeline

The download script (`Download-Media.ps1`) handles transferring files from a remote seedbox to the local media server via FTP using WinSCP.

### Flow

```
Remote Seedbox (FTP)
        |
        v
Step 1: Enumerate remote files
        - Filter: video extensions only
        - Filter: skip sample files
        - Filter: skip files newer than 1 minute (age filter)
        |
        v
Step 2: Download files
        - Parallel mode (8 slots) when more than 1 file
        - Per-file verify: check file exists and size > 0
        - Per-file remote delete after successful verify
        - Unique WinSCP log file per parallel worker
        |
        v
Step 3: Clean up remote directories
        - Remove empty directories
        - Remove directories containing only stale non-video files
        - Never delete directories newer than age threshold
        - Never delete protected directory names
        |
        v
Step 4: Backup landing folder
        - Copy all files to backup location before moving
        |
        v
Step 5: Move to Finished folder
        - robocopy /MOV preserves subfolder structure
        - Check robocopy exit code (warn if >= 8)
        |
        v
Step 6: Trigger arr import scans
        - Sonarr: DownloadedEpisodesScan on Finished folder
        - Radarr: DownloadedMoviesScan on Finished folder
```

### Key Settings

```powershell
$ParallelSlots      = 8        # Max simultaneous downloads
$MinFileAgeMinutes  = 1        # Skip files newer than this
$LargeFileThreshold = 1GB      # Used for logging only
```

### Protected Directory Names

Folder names in this list are stripped from the relative path when building the local destination. This prevents wrapper folder names from the remote server appearing in the local file structure.

```powershell
$ProtectedDirNames = @("tv-sonarrpub", "tv-sonarrpriv", "radarrpub", "radarrpriv")
```

### Robocopy Exit Codes

Robocopy uses non-standard exit codes. Code 1 means "files copied successfully"  -  PowerShell treats this as an error. The script only warns on exit code 8 or higher, which indicates actual failures.

| Code | Meaning |
|------|---------|
| 0 | No files copied, no errors |
| 1 | Files copied successfully |
| 2 | Extra files detected |
| 4 | Mismatched files |
| 8+ | Errors occurred |

### Why the Arr Scan Trigger Matters

Files arriving via FTP have no grab history in Sonarr/Radarr. The arr never issued the download command, so it cannot automatically match the file to a monitored series or movie. The `DownloadedEpisodesScan` and `DownloadedMoviesScan` API commands force the arr to actively scan the folder and attempt matching using its full parsing engine, which handles more naming variations than the passive watcher.

Without this trigger, files sit in the Finished folder indefinitely and appear in the Import Failures section of the MediaSentinel report.

---

## Tdarr GPU Encoding Setup

Tdarr handles automated media processing  -  health checks and re-encoding.

### Architecture

Tdarr has two components that run separately:
- **Tdarr Server** - Web UI, database, job queue management
- **Tdarr Node** - The worker that actually processes files

Both should run as the same user account that has access to the GPU. Running as SYSTEM account will prevent GPU access.

### Plugin Stack

The order of plugins in the stack matters. Each plugin either processes the file and passes it to the next plugin, or skips/stops processing. A well-configured stack:

```
1. Migz Remove Image Formats From File
        Strips embedded cover art and image attachments that cause
        some encoders to fail or produce corrupt output.

2. Lmg1 Reorder Streams
        Ensures stream order is video first, audio second, subtitles
        last. Some clients have issues with non-standard stream ordering.

3. Filter By Video Codec (filter out: hevc, h265)
        Skips files already in HEVC/H.265. Without this, Tdarr will
        re-encode files that were already converted, wasting time and
        degrading quality with each generation.

4. Transcode A Video File (encoder: hevc_qsv)
        Re-encodes video to HEVC using Intel QSV hardware acceleration.
        Only reaches this step if the file passed the codec filter.

5. New File Size Check
        Verifies the output file is not significantly larger than the
        input. If the output is larger, the transcode is rejected and
        the original is kept.
```

### Intel QSV Configuration

Intel Quick Sync Video (QSV) requires:
- Intel Arc, Iris Xe, or integrated graphics with recent drivers
- Intel VPL (Video Processing Library) runtime installed
- FFmpeg compiled with `--enable-libvpl`

To verify QSV is available:
```powershell
& "PATH_TO_FFMPEG\ffmpeg.exe" -hide_banner -init_hw_device qsv=qsv:hw -filter_hw_device qsv
```

Expected output: `Using device XXXX:XXXX (Intel(R) ...)` with no error.

### Verifying GPU Is Actually Encoding

Tdarr's node stats panel does not show Intel GPU usage (only NVIDIA via NVML). To verify the Arc is encoding:

1. Open Task Manager > Performance > GPU while a transcode is running
2. The GPU utilization graph should spike during encoding
3. If only CPU spikes and GPU stays flat, QSV is not being used

### Health Checks

Tdarr's health check uses ffprobe to inspect files for stream errors. It catches:
- Missing or corrupt video/audio streams
- Container format errors
- Codec errors

It does NOT catch:
- Broken Matroska keyframe metadata (seek table issues)
- Files that are technically valid but will fail during HLS streaming

Broken Matroska seek tables are caught by MediaSentinel's playback error detector after a failed playback attempt, not by Tdarr health checks proactively.

### Common Tdarr Issues

**"Safety check: new transcode arguments were the exact same as the last ones"**

The HandBrake Or FFmpeg Custom Arguments plugin stores previous arguments and refuses to run if they match the current arguments, to prevent infinite loops. 

*Fix:* Use a different plugin (Transcode A Video File) instead of the custom arguments plugin. If you must use custom arguments, add a harmless unique flag like `-metadata TDARR=VERSION_NUMBER` and increment it when changing the plugin.

**GPU workers configured but CPU encoding**

Causes in order of likelihood:
1. Plugin is not calling a hardware encoder (`hevc_qsv`, `h264_qsv`) - check plugin settings
2. Tdarr's bundled FFmpeg doesn't support the GPU API - point Tdarr to Jellyfin's FFmpeg
3. Node is running as SYSTEM account - change to user account with GPU access
4. Wrong GPU API for the OS - VAAPI is Linux only, QSV is Windows/Linux, NVENC is NVIDIA only

**VAAPI errors on Windows**

VAAPI is a Linux GPU API. Any plugin or FFmpeg argument using `-hwaccel vaapi` will fail on Windows. For Intel GPU on Windows the correct API is QSV (`-hwaccel qsv`, encoder `hevc_qsv`).

### Auto-Start with Task Scheduler

```powershell
# Server task
$action   = New-ScheduledTaskAction -Execute "PATH_TO\Tdarr_Server.exe"
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "YOUR_USERNAME" -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "Tdarr Server" -TaskPath "\Tdarr\" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force

# Node task (10 minute delay to let server start)
$nodeAction  = New-ScheduledTaskAction -Execute "PATH_TO\Tdarr_Node.exe"
$nodeTrigger = New-ScheduledTaskTrigger -AtStartup
$nodeTrigger.Delay = "PT10M"
Register-ScheduledTask -TaskName "Tdarr Node" -TaskPath "\Tdarr\" -Action $nodeAction -Trigger $nodeTrigger -Settings $settings -Principal $principal -Force
```

---

## Task Scheduler

All MediaSentinel tasks are under `\MediaSentinel\` in Task Scheduler.

| Task | Schedule | What it runs |
|------|----------|-------------|
| MediaSentinel Monitor | Every 15 min | run_monitor.ps1 |
| MediaSentinel Daily Recommendations | Daily 6:00am | run_monitor.ps1 -WithRecommendations |
| MediaSentinel Weekly Transcode Report | Sunday 6:30am | weekly_transcode_report.py |
| MediaSentinel HTTP Server | On boot | python -m http.server 8765 |
| Tdarr Server | On boot | Tdarr_Server.exe |
| Tdarr Node | On boot + 10min delay | Tdarr_Node.exe |

### Important Task Scheduler Settings

- All tasks should run with highest privileges
- Tasks that access drives or services should run whether user is logged on or not
- The HTTP server task must stay running (set execution time limit to 0)
- Use full Python path in all tasks - Windows Store Python stub blocks the `python` command

---

## One-Liners and Useful Commands

Replace `PATH_TO_PYTHON` with your full Python executable path and `PATH_TO_MEDIASENTINEL` with your install directory.

### Full Manual Run

Runs the complete pipeline: SMART collection, data collection, AI analysis, report generation, recommendations page.

```powershell
$py="PATH_TO_PYTHON"; $logs="PATH_TO_MEDIASENTINEL\logs"; $ts=(Get-Date -Format "yyyyMMddTHHmmss"); $data="$logs\data_$ts.json"; $analysis="$logs\analysis_$ts.json"; $html="$logs\report_latest.html"; $rec="$logs\recommendations.html"; $utf8=[System.Text.UTF8Encoding]::new($false); [System.IO.File]::WriteAllText("$logs\smart_cache.json", (& $py -W ignore PATH_TO_MEDIASENTINEL\scripts\collect_smart.py 2>$null) -join "`n", $utf8); [System.IO.File]::WriteAllText($data, (& $py -W ignore PATH_TO_MEDIASENTINEL\scripts\collect_data.py 2>$null) -join "`n", $utf8); [System.IO.File]::WriteAllText($analysis, (& $py -W ignore PATH_TO_MEDIASENTINEL\scripts\analyze.py $data 2>$null) -join "`n", $utf8); [System.IO.File]::WriteAllText($html, (& $py -W ignore PATH_TO_MEDIASENTINEL\scripts\render_report.py $analysis $data 2>$null) -join "`n", $utf8); [System.IO.File]::WriteAllText($rec, (& $py -W ignore PATH_TO_MEDIASENTINEL\scripts\render_recommendations_page.py $data 2>$null) -join "`n", $utf8); [Console]::WriteLine("Report: $html"); [Console]::WriteLine("Recommendations: $rec")
```

### Test Individual Collectors

```powershell
$py = "PATH_TO_PYTHON"

# Test import failure detection
& $py "PATH_TO_MEDIASENTINEL\scripts\collect_import_failures.py"

# Test playback error detection
& $py "PATH_TO_MEDIASENTINEL\scripts\collect_playback_errors.py"

# Test data collection (full)
& $py "PATH_TO_MEDIASENTINEL\scripts\collect_data.py"

# Force log transcode history against latest data file
$latest = Get-ChildItem "PATH_TO_MEDIASENTINEL\logs\data_*.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
& $py "PATH_TO_MEDIASENTINEL\scripts\log_transcodes.py" $latest.FullName

# Generate weekly transcode report manually
& $py "PATH_TO_MEDIASENTINEL\scripts\weekly_transcode_report.py"
```

### Database Queries

Always write queries to a temp file due to PowerShell quote escaping:

```powershell
$py = "PATH_TO_PYTHON"
$db = "PATH_TO_MEDIASENTINEL\logs\transcodes.db"
$qf = "PATH_TO_MEDIASENTINEL\logs\query.py"

# Check database tables
@"
import sqlite3
conn = sqlite3.connect(r'$db')
print(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
conn.close()
"@ | Out-File $qf -Encoding utf8; & $py $qf

# All records newest first
@"
import sqlite3
conn = sqlite3.connect(r'$db')
conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT * FROM transcode_sessions ORDER BY date DESC').fetchall():
    print(dict(r))
conn.close()
"@ | Out-File $qf -Encoding utf8; & $py $qf

# Top transcoded titles (last 30 days)
@"
import sqlite3
conn = sqlite3.connect(r'$db')
for r in conn.execute("SELECT title, COUNT(*) c FROM transcode_sessions WHERE date >= date('now','-30 days') GROUP BY title ORDER BY c DESC LIMIT 20").fetchall():
    print(f'{r[1]:>4}x  {r[0]}')
conn.close()
"@ | Out-File $qf -Encoding utf8; & $py $qf

# Transcode reasons breakdown
@"
import sqlite3
conn = sqlite3.connect(r'$db')
for r in conn.execute("SELECT transcode_reasons, COUNT(*) c FROM transcode_sessions GROUP BY transcode_reasons ORDER BY c DESC").fetchall():
    print(f'{r[1]:>4}x  {r[0]}')
conn.close()
"@ | Out-File $qf -Encoding utf8; & $py $qf

# Source codec breakdown
@"
import sqlite3
conn = sqlite3.connect(r'$db')
for r in conn.execute("SELECT source_video_codec, source_audio_codec, COUNT(*) c FROM transcode_sessions GROUP BY source_video_codec, source_audio_codec ORDER BY c DESC").fetchall():
    print(f'{r[2]:>4}x  video:{r[0]}  audio:{r[1]}')
conn.close()
"@ | Out-File $qf -Encoding utf8; & $py $qf
```

### Verify QSV / GPU Access

```powershell
# Test Intel QSV availability
$ffmpeg = "PATH_TO_JELLYFIN\ffmpeg.exe"
& $ffmpeg -hide_banner -init_hw_device qsv=qsv:hw -filter_hw_device qsv

# Check GPU encode engine utilization (run while Tdarr is encoding)
Get-Counter "\GPU Engine(*)\Utilization Percentage" |
    Select-Object -ExpandProperty CounterSamples |
    Where-Object CookedValue -gt 1 |
    Select-Object InstanceName, CookedValue |
    Sort-Object CookedValue -Descending

# Find all FFmpeg executables on the system
Get-ChildItem "C:\" -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object FullName
```

### Jellyfin Log Analysis

```powershell
$logdir = "C:\ProgramData\Jellyfin\Server\log"

# List recent log files
Get-ChildItem $logdir | Sort-Object LastWriteTime -Descending | Select-Object Name, Length, LastWriteTime | Select-Object -First 20

# Find playback errors in last night's log (evening hours)
Select-String -Path "$logdir\log_$(Get-Date -Format 'yyyyMMdd').log" -Pattern "\[ERR\]|Exception|failed|abort|disconnect|timeout" |
    Where-Object { $_.Line -match "\[$(Get-Date -Format 'yyyy-MM-dd') (2[0-9]|1[89]):" } |
    ForEach-Object { $_.Line }

# List FFmpeg sessions with duration (short sessions = crash loops)
Get-ChildItem "$logdir\FFmpeg.Transcode-$(Get-Date -Format 'yyyy-MM-dd')*.log" |
    Sort-Object Name |
    Select-Object Name, Length, @{N="DurationSec";E={[int]($_.LastWriteTime - $_.CreationTime).TotalSeconds}} |
    Format-Table -AutoSize
```

### Fix Broken MKV Keyframe Metadata

When the playback error section flags a file with MatroskaKeyframeExtractor failures:

```powershell
$ffmpeg = "PATH_TO_JELLYFIN\ffmpeg.exe"
$src    = "PATH_TO_BROKEN_FILE.mkv"
$dst    = "PATH_TO_FIXED_FILE.mkv"

# Remux without re-encoding (fast, ~2 min for 1hr file)
& $ffmpeg -i $src -c copy $dst

# Verify the fixed file, then replace original
# Test plays correctly in Jellyfin before deleting the original
```

Note: Re-encoding the file to HEVC via Tdarr also fixes this as a side effect.

---

## Troubleshooting

### Report is blank or cuts off mid-section

**Cause:** A Python source file contains non-ASCII characters (Unicode arrows, em dashes, emoji) that get corrupted in the PowerShell to Python stdout pipeline on Windows.

**Diagnosis:**
```powershell
# Check a script for non-ASCII bytes
python -c "data=open('SCRIPT.py','rb').read(); bad=[b for b in data if b>127]; print(f'DIRTY: {len(bad)} bytes' if bad else 'CLEAN')"
```

**Fix:** Replace all non-ASCII characters in .py files with HTML entities (`&#8599;` for arrows) or plain ASCII equivalents. This applies to all Python scripts that produce HTML output.

### Report cuts off at angle bracket text

**Cause:** A diagnosis or description string contains literal `<word>` angle brackets written directly into HTML. The browser interprets them as unknown HTML tags and hides everything after them.

**Fix:** Remove angle brackets from any string that goes into HTML output. Use plain text: `Title (Year)` instead of `<Title> (<Year>)`.

### Import failures always showing the same items

**Cause:** Files are arriving via FTP without grab history, so arr cannot match them automatically even after multiple scan attempts.

**Fix:** Use Manual Import in Sonarr/Radarr Activity > Queue for stubborn files. Ensure the Download-Media.ps1 arr scan trigger is running after each transfer.

### Transcode database is empty

**Cause:** No one was actively transcoding when the collector ran.

**Fix:** The database populates automatically during active transcode sessions. Check that `log_transcodes.py` is being called from `run_monitor.ps1`. Run it manually against a data file captured during active playback.

### Sonarr/Radarr API returning partial results

**Cause:** URL encoding bug in Jellyfin's `jf_get()` helper function drops certain query parameters silently.

**Fix:** Use direct `requests.Session()` with manual URL construction for any Jellyfin API call that uses `HasTmdbId=true` or similar parameters. Never use the `jf_get()` helper for library scans.

### LM Studio not responding

**Cause:** NordVPN or other VPN software creates a secondary network interface that LM Studio binds to instead of the LAN interface.

**Fix:** In LM Studio settings, explicitly bind to the LAN IP address rather than `0.0.0.0`. Use the Wi-Fi or ethernet interface IP, not any VPN tunnel address.

### PowerShell script output garbled or missing

**Cause:** `Write-Host` output is swallowed in non-interactive sessions (Task Scheduler).

**Fix:** Use `[Console]::WriteLine()` for all output in scripts called by Task Scheduler.

### Python not found in Task Scheduler

**Cause:** Windows Store installs a Python stub that intercepts the `python` command and opens the Store instead of running Python.

**Fix:** Always use the full Python executable path in Task Scheduler actions and PowerShell scripts.

---

## Known Limitations

**Tdarr health checks do not catch broken Matroska seek tables.** FFprobe's basic health check confirms the file is readable and streams are valid, but does not verify the seek index. Broken keyframe metadata only manifests during HLS streaming when Jellyfin tries to build a segment index. MediaSentinel's playback error detector catches this after a failed playback attempt.

**Recommendations require TMDB API access.** The recommendation engine makes outbound API calls to TMDB. If the server has no internet access or TMDB is unavailable, recommendations will be empty. The tmdb_cache.json file preserves previous results between runs.

**SMART data requires Administrator.** `collect_smart.py` must run with elevated privileges. If the Task Scheduler task does not have "Run with highest privileges" enabled, SMART data will be missing or incomplete.

**Transcode history only captures active sessions.** Health check runs, direct play sessions, and sessions that start and stop between 15-minute collection intervals will not be logged.

**Android TV app does not support Jellyfin plugin injection.** The MediaSentinel TV app is a standalone Android app. The Jellyfin custom tab approach (embedding the report via iframe) only works in the Jellyfin web interface, not in Flutter-based clients like Moonfin.

**AI analysis quality depends on the local model.** The system is designed for any OpenAI-compatible local AI endpoint (LM Studio, Ollama, Jan, llama.cpp). Smaller models may produce lower quality analysis or miss subtle issues in the data.

---

## Technical Decisions and Lessons Learned

**1. Jellyfin library scan must use direct requests, not jf_get()**
The `jf_get()` helper uses PreparedRequest which silently drops certain query parameters like `HasTmdbId=true`. Always use direct `requests.Session()` for library scans. The symptom is getting fewer results than expected (e.g. 684 of 904 items).

**2. PowerShell file writing must avoid BOM**
`Out-File` and `Set-Content` add a UTF-8 BOM that breaks Python's JSON parser. Always use `[System.IO.File]::WriteAllText(path, content, System.Text.UTF8Encoding($false))` for files that Python will read.

**3. All Python source files must be pure ASCII**
Any Unicode character in a .py file (em dash, arrow, emoji) gets corrupted in the PowerShell to Python stdout pipeline on Windows. Use HTML entities in HTML output strings and plain ASCII in all other contexts.

**4. HTML strings must not contain raw angle brackets**
Strings like `<Title>` or `<Year>` written directly into HTML output cause the browser to interpret them as tags, hiding all subsequent content. Always use plain text without angle brackets in any string destined for HTML.

**5. AI recommendations use numbered lists to prevent hallucination**
The AI is sent `1. Title One\n2. Title Two\n...` and asked to return only `{"num": N, "reason": "..."}`. Never ask the AI to generate titles or metadata it could fabricate. The reason field is the only AI-generated content.

**6. TMDB year filters prevent classic content domination**
Without date filters, TMDB similar/recommended returns highly-rated classics for every query. Genre discover uses 2021+, actor discover uses 2018+, item-based uses 2019+.

**7. robocopy exit code 1 is success**
Robocopy returns 1 for "files copied successfully" which PowerShell treats as an error. Only warn on exit code 8 or higher.

**8. Parallel WinSCP sessions need unique log files**
Multiple runspaces writing to the same WinSCP session log simultaneously causes corruption and file lock errors. Use per-file log paths derived from the filename.

**9. SQLite table is transcode_sessions not transcodes**
Always verify table names before querying an unfamiliar SQLite database: `SELECT name FROM sqlite_master WHERE type='table'`. Use PowerShell here-strings written to temp files rather than inline -c Python arguments to avoid quote escaping issues.

**10. Tdarr VAAPI is Linux only**
VAAPI is a Linux GPU API. Any Tdarr plugin using `-hwaccel vaapi` will fail on Windows regardless of GPU. On Windows with Intel GPU use QSV: `-hwaccel qsv`, encoder `hevc_qsv`. On Windows with NVIDIA use NVENC: encoder `hevc_nvenc`.

**11. Tdarr custom arguments plugin has a loop detection bug**
The HandBrake Or FFmpeg Custom Arguments plugin compares the current job arguments to the previous job arguments and refuses to run if they match. This triggers incorrectly after DB resets. Use the Transcode A Video File plugin instead which does not have this issue.

**12. Playback crash loop signature**
In FFmpeg session logs: multiple sessions for the same media, each lasting 5-15 seconds, starting within seconds of each other. In Jellyfin server log: bursts of MatroskaKeyframeExtractor failures followed by `FFmpeg exited with code -1` followed by rapid task-canceled errors on sequential .ts segment numbers.

**13. Download script bypasses arr grab history**
Files transferred via FTP arrive with no Sonarr/Radarr grab history. The arr must parse folder names cold which fails on non-standard naming. Fix: call `DownloadedEpisodesScan` and `DownloadedMoviesScan` API commands after delivery to trigger active matching.

**14. Intel Arc requires VPL runtime not legacy Media SDK**
Arc GPUs need the Intel oneAPI Video Processing Library (VPL) runtime. The older Intel Media SDK that works with older integrated graphics may not initialize Arc correctly. FFmpeg must be compiled with `--enable-libvpl`. Verify with: `ffmpeg -hide_banner -init_hw_device qsv=qsv:hw`.
