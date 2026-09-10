# -*- coding: utf-8 -*-
"""
collect_playback_errors.py
MediaSentinel - Jellyfin playback error detector

Parses today's and yesterday's Jellyfin server log plus all FFmpeg session logs
to identify playback incidents: crash loops, keyframe failures, missing cache
segments, FFmpeg exits, and task cancellations.

Returns a dict with:
  incidents       - list of incident dicts, deduplicated and grouped
  total_incidents - int
  collected_at    - ISO timestamp
"""

import os
import re
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"
DEFAULT_LOG_DIR = Path(r"C:\ProgramData\Jellyfin\Server\log")
LOOK_BACK_HOURS = 24

def _get_log_dir(cfg=None):
    """Return the Jellyfin log directory from config, or the Windows default."""
    if cfg is None:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    log_dir = cfg.get("paths", {}).get("jellyfin_log_dir")
    return Path(log_dir) if log_dir else DEFAULT_LOG_DIR

# ---------------------------------------------------------------------------
# Known error patterns and their human-readable diagnosis + fix
# ---------------------------------------------------------------------------
PATTERNS = [
    {
        "id":      "matroska_keyframe",
        "regex":   re.compile(r'MatroskaKeyframeExtractor.*Extracting keyframes from "([^"]+)" using matroska metadata failed', re.IGNORECASE),
        "title":   "Broken MKV keyframe metadata",
        "cause":   "The MKV file has a corrupt or missing Matroska seek table. "
                   "Jellyfin cannot build a seek index, causing FFmpeg to crash when "
                   "the client tries to seek or when HLS segments are requested.",
        "severity": "CRITICAL",
        "fix": [
            "Remux the file to rebuild the container index without re-encoding:",
            'ffmpeg -i "SOURCE.mkv" -c copy "SOURCE.fixed.mkv"',
            "Verify the fixed file plays correctly, then replace the original.",
            "Run Jellyfin library scan after replacing the file.",
        ],
    },
    {
        "id":      "ffmpeg_crash",
        "regex":   re.compile(r'FFmpeg exited with code -1', re.IGNORECASE),
        "title":   "FFmpeg crashed during transcode",
        "cause":   "FFmpeg terminated abnormally with exit code -1. Usually triggered "
                   "by corrupt input file metadata, a codec the installed FFmpeg build "
                   "cannot handle, or the transcode cache running out of disk space.",
        "severity": "CRITICAL",
        "fix": [
            "Check the FFmpeg log for the session (same timestamp) for the actual error.",
            "If paired with MatroskaKeyframeExtractor errors, remux the MKV file.",
            "Check C: drive free space - transcode cache fills up and can cause this.",
            "Update Jellyfin FFmpeg if the codec is unsupported.",
        ],
    },
    {
        "id":      "missing_ts_segment",
        "regex":   re.compile(r"Could not find file '([^']*\.ts)'.*URL.*hls.*main/(\d+)\.ts", re.IGNORECASE),
        "title":   "Missing HLS transcode segment",
        "cause":   "The client requested an HLS segment (.ts file) that no longer "
                   "exists in the transcode cache. Jellyfin's cache cleanup task "
                   "deleted it while the client was still playing, or a previous "
                   "FFmpeg crash left the cache in a partial state.",
        "severity": "WARNING",
        "fix": [
            "Check C: drive free space - Jellyfin aggressively clears cache when low.",
            "In Jellyfin Dashboard > Playback, increase the transcode cache size limit.",
            "If this happens repeatedly on the same file, check for FFmpeg crash errors too.",
            "Consider moving the transcode cache to K:\\ if C:\\ is constrained.",
        ],
    },
    {
        "id":      "task_canceled",
        "regex":   re.compile(r'Error processing request: "A task was canceled".*hls.*main/(\d+)\.ts', re.IGNORECASE),
        "title":   "HLS segment request canceled",
        "cause":   "The client canceled a segment request before the server could "
                   "respond. In small numbers this is normal (seeking). When it "
                   "occurs in rapid bursts on sequential segment numbers it indicates "
                   "the client is in a restart loop after a playback failure.",
        "severity": "INFO",
        "fix": [
            "A few cancellations per session are normal seeking behavior.",
            "Bursts of 5+ cancellations in under 60 seconds indicate a crash loop - "
            "look for a paired FFmpeg crash or missing segment error at the same time.",
            "If persistent: check network stability between client and server.",
        ],
    },
    {
        "id":      "omdb_error",
        "regex":   re.compile(r'EpisodeMetadataService.*Error in "The Open Movie Database"', re.IGNORECASE),
        "title":   "OMDB metadata fetch failure",
        "cause":   "The Open Movie Database API returned invalid JSON. This is an "
                   "OMDB service issue or API key rate limit, not a playback problem. "
                   "It does not affect playback.",
        "severity": "INFO",
        "fix": [
            "No action needed - this does not affect playback.",
            "If persistent: disable OMDB as a metadata provider in Jellyfin Dashboard "
            "> Libraries > (library) > Metadata > uncheck The Open Movie Database.",
        ],
    },
]

# ---------------------------------------------------------------------------
# FFmpeg session log analyser
# ---------------------------------------------------------------------------
FFMPEG_ERROR_PATTERNS = [
    re.compile(r'Error.*|error.*|Invalid.*|corrupt.*|moov atom not found', re.IGNORECASE),
]

def analyse_ffmpeg_log(log_path):
    """
    Read an FFmpeg session log and return a list of notable error lines.
    Ignores normal progress lines (frame=, fps=, opening .ts files).
    """
    skip = re.compile(r'^(frame=|fps=|\[hls|ffmpeg version|built with|configuration:|lib|Input #|Output #|Stream mapping|Press|video:|audio:|Metadata:|Duration:|Stream #)', re.IGNORECASE)
    errors = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or skip.match(line):
                    continue
                if any(p.search(line) for p in FFMPEG_ERROR_PATTERNS):
                    errors.append(line[:300])
    except Exception:
        pass
    return errors[:10]  # cap at 10 lines per session


def get_ffmpeg_sessions(since_dt, log_dir=None):
    """
    Return a dict of session_id -> {start, duration_sec, errors, log_path}
    for all FFmpeg logs newer than since_dt.
    """
    if log_dir is None:
        log_dir = _get_log_dir()
    sessions = {}
    if not log_dir.exists():
        return sessions

    for f in log_dir.glob("FFmpeg.Transcode-*.log"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            ctime = datetime.fromtimestamp(f.stat().st_ctime, tz=timezone.utc)
            if ctime < since_dt:
                continue
            duration = int((mtime - ctime).total_seconds())
            # Extract session ID from filename
            # Format: FFmpeg.Transcode-YYYY-MM-DD_HH-MM-SS_<sessionid>_<hash>.log
            parts = f.stem.split("_")
            session_id = parts[3] if len(parts) > 3 else "unknown"
            start_str  = f"{parts[1]}_{parts[2]}" if len(parts) > 2 else ""
            errors = analyse_ffmpeg_log(f)
            sessions[f.name] = {
                "log_path":    str(f),
                "session_id":  session_id,
                "start":       start_str.replace("_", " ").replace("-", ":"),
                "duration_sec": duration,
                "errors":      errors,
                "is_crash":    duration < 15 and len(errors) > 0,
            }
        except Exception:
            continue
    return sessions


# ---------------------------------------------------------------------------
# Main Jellyfin log parser
# ---------------------------------------------------------------------------

def parse_jellyfin_log(log_path, since_dt):
    """
    Parse a Jellyfin server log file and return raw matched events.
    Each event: {timestamp, pattern_id, file_path, extra, raw_line}
    """
    events = []
    ts_re  = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m_ts = ts_re.search(line)
                if not m_ts:
                    continue
                try:
                    line_dt = datetime.fromisoformat(m_ts.group(1)).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if line_dt < since_dt:
                    continue

                for pat in PATTERNS:
                    m = pat["regex"].search(line)
                    if not m:
                        continue
                    # Extract file path or segment number from capture groups
                    file_path = ""
                    extra     = ""
                    if m.lastindex and m.lastindex >= 1:
                        g1 = m.group(1)
                        # Is it a file path or a segment number?
                        if g1.startswith(("K:\\", "C:\\", "/")):
                            file_path = g1
                        else:
                            extra = g1
                    if m.lastindex and m.lastindex >= 2:
                        extra = m.group(2)

                    events.append({
                        "timestamp":  line_dt.isoformat(),
                        "pattern_id": pat["id"],
                        "file_path":  file_path,
                        "extra":      extra,
                        "raw_line":   line.strip()[:300],
                    })
                    break  # only match one pattern per line
    except Exception as e:
        sys.stderr.write(f"[playback_errors] failed to parse {log_path}: {e}\n")

    return events


# ---------------------------------------------------------------------------
# Incident grouping
# ---------------------------------------------------------------------------

def group_into_incidents(events, ffmpeg_sessions):
    """
    Group raw events into incidents.

    Rules:
    - MatroskaKeyframeExtractor bursts on the same file within 5 min = 1 incident
    - task_canceled bursts within 60 sec = 1 incident (crash loop)
    - ffmpeg_crash = always its own incident
    - missing_ts_segment within 60 sec = 1 incident
    - omdb_error: group all into one incident per day
    - Crash loops (7+ task_canceled in <60s) are upgraded to CRITICAL
    """
    incidents = []

    # Group by pattern + file, with time windowing
    by_pattern = defaultdict(list)
    for e in events:
        key = (e["pattern_id"], e["file_path"])
        by_pattern[key].append(e)

    for (pattern_id, file_path), evts in by_pattern.items():
        pat = next(p for p in PATTERNS if p["id"] == pattern_id)
        evts = sorted(evts, key=lambda x: x["timestamp"])

        # Window grouping
        window_minutes = {
            "matroska_keyframe": 10,
            "task_canceled":     2,
            "missing_ts_segment": 2,
            "ffmpeg_crash":      1,
            "omdb_error":        1440,  # group entire day
        }.get(pattern_id, 5)

        groups = []
        current_group = [evts[0]]
        for evt in evts[1:]:
            try:
                last_dt = datetime.fromisoformat(current_group[-1]["timestamp"])
                this_dt = datetime.fromisoformat(evt["timestamp"])
                if (this_dt - last_dt).total_seconds() <= window_minutes * 60:
                    current_group.append(evt)
                else:
                    groups.append(current_group)
                    current_group = [evt]
            except Exception:
                current_group.append(evt)
        groups.append(current_group)

        for group in groups:
            first     = group[0]
            last      = group[-1]
            count     = len(group)
            severity  = pat["severity"]
            title     = pat["title"]

            # Upgrade crash loops
            if pattern_id == "task_canceled" and count >= 5:
                severity = "CRITICAL"
                title    = f"Playback crash loop ({count} segment failures)"

            # Extract media title from file path
            media_title = ""
            if file_path:
                p = Path(file_path)
                media_title = p.stem

            # Find any FFmpeg sessions that crashed around the same time
            related_ffmpeg = []
            try:
                first_dt = datetime.fromisoformat(first["timestamp"])
                for fname, sess in ffmpeg_sessions.items():
                    if sess["is_crash"] and sess["errors"]:
                        related_ffmpeg.append({
                            "log": fname,
                            "duration_sec": sess["duration_sec"],
                            "errors": sess["errors"],
                        })
            except Exception:
                pass

            incidents.append({
                "pattern_id":      pattern_id,
                "title":           title,
                "severity":        severity,
                "cause":           pat["cause"],
                "fix":             pat["fix"],
                "file_path":       file_path,
                "media_title":     media_title,
                "first_seen":      first["timestamp"],
                "last_seen":       last["timestamp"],
                "occurrence_count": count,
                "related_ffmpeg":  related_ffmpeg,
            })

    # Sort: CRITICAL first, then WARNING, then INFO, then by time
    sev_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    incidents.sort(key=lambda x: (sev_order.get(x["severity"], 3), x["first_seen"]))

    return incidents


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def collect_playback_errors(cfg=None):
    log_dir  = _get_log_dir(cfg)
    since_dt = datetime.now(timezone.utc) - timedelta(hours=LOOK_BACK_HOURS)
    all_events = []

    if not log_dir.exists():
        return {
            "incidents":        [],
            "total_incidents":  0,
            "error":            f"Log directory not found: {log_dir}",
            "collected_at":     datetime.now(timezone.utc).isoformat(),
        }

    # Parse today's and yesterday's Jellyfin server logs
    today     = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    for date_str in [today, yesterday]:
        log_path = log_dir / f"log_{date_str}.log"
        if log_path.exists():
            all_events.extend(parse_jellyfin_log(log_path, since_dt))

    # Get FFmpeg session data
    ffmpeg_sessions = get_ffmpeg_sessions(since_dt, log_dir)

    # Group into incidents
    incidents = group_into_incidents(all_events, ffmpeg_sessions)

    # Crash loop summary from FFmpeg sessions
    crash_loops = [
        s for s in ffmpeg_sessions.values()
        if s["duration_sec"] < 15
    ]

    return {
        "incidents":           incidents,
        "total_incidents":     len(incidents),
        "crash_loop_sessions": len(crash_loops),
        "ffmpeg_session_count": len(ffmpeg_sessions),
        "collected_at":        datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    result = collect_playback_errors()
    print(json.dumps(result, indent=2))
