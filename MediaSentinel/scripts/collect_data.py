#!/usr/bin/env python3
"""
collect_data.py
Polls all media stack services and returns a unified JSON payload
for analysis by the local AI model.
"""

import json
import sys
import requests
import subprocess
import datetime
import os
import socket
import importlib.util
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def safe_get(url, headers=None, timeout=8):
    """HTTP GET with timeout and error capture."""
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError as e:
        return None, f"CONNECTION_ERROR: {e}"
    except requests.exceptions.Timeout:
        return None, "TIMEOUT"
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP_{r.status_code}: {e}"
    except Exception as e:
        return None, str(e)


# -- Jellyfin ------------------------------------------------------------------

def collect_jellyfin(cfg):
    base = cfg["services"]["jellyfin"]["url"]
    key  = cfg["services"]["jellyfin"]["api_key"]
    h    = {"X-Emby-Authorization": f'MediaBrowser Token="{key}"'}
    out  = {"service": "jellyfin", "errors": []}

    # Active sessions / transcoding
    sessions, err = safe_get(f"{base}/Sessions", headers=h)
    if err:
        out["errors"].append({"source": "sessions", "error": err})
        out["active_sessions"] = []
    else:
        out["active_sessions"] = []
        out["active_transcodes"] = []
        for s in (sessions or []):
            item  = s.get("NowPlayingItem", {}) or {}
            ti    = s.get("TranscodingInfo") or {}
            state = s.get("PlayState", {}) or {}

            entry = {
                "user":        s.get("UserName"),
                "client":      s.get("Client"),
                "device":      s.get("DeviceName"),
                "now_playing": item.get("Name"),
                "media_type":  item.get("Type"),
                "play_method": state.get("PlayMethod"),
            }
            out["active_sessions"].append(entry)

            if ti:
                # Pull full file stream details
                streams   = item.get("MediaStreams", []) or []
                vid_stream = next((x for x in streams if x.get("Type") == "Video"), {})
                aud_stream = next((x for x in streams if x.get("Type") == "Audio" and x.get("IsDefault")), {})
                if not aud_stream:
                    aud_stream = next((x for x in streams if x.get("Type") == "Audio"), {})

                file_video = {
                    "codec":       vid_stream.get("Codec"),
                    "profile":     vid_stream.get("Profile"),
                    "resolution":  f"{vid_stream.get('Width')}x{vid_stream.get('Height')}",
                    "bit_depth":   vid_stream.get("BitDepth"),
                    "bitrate_mbps": round(vid_stream.get("BitRate", 0) / 1_000_000, 1),
                    "hdr_type":    vid_stream.get("VideoRangeType"),
                    "color_space": vid_stream.get("ColorSpace"),
                }
                file_audio = {
                    "codec":    aud_stream.get("Codec"),
                    "profile":  aud_stream.get("Profile"),
                    "channels": aud_stream.get("Channels"),
                    "layout":   aud_stream.get("ChannelLayout"),
                    "title":    aud_stream.get("Title"),
                }

                # What Jellyfin is transcoding TO
                transcode_to = {
                    "video_codec":  ti.get("VideoCodec"),
                    "audio_codec":  ti.get("AudioCodec"),
                    "container":    ti.get("Container"),
                    "resolution":   f"{ti.get('Width')}x{ti.get('Height')}",
                    "bitrate_mbps": round((ti.get("Bitrate") or 0) / 1_000_000, 1),
                    "audio_channels": ti.get("AudioChannels"),
                    "hw_accel":     ti.get("HardwareAccelerationType"),
                    "video_direct": ti.get("IsVideoDirect"),
                    "audio_direct": ti.get("IsAudioDirect"),
                }

                # Roku client direct play limits from CodecProfiles
                client_limits = {
                    "h264_max_bitrate_mbps": 10,
                    "h264_max_range":        "SDR only (VideoRangeType must be SDR|DOVIWithSDR)",
                    "h264_max_profile":      "high|main",
                    "hevc_max_bitrate_mbps": 40,
                    "hevc_range_support":    "SDR+HDR10+HLG+DOVI",
                    "audio_max_channels":    2,
                }

                reasons = ti.get("TranscodeReasons", [])

                # Human-readable explanation of each reason
                reason_detail = []
                if "AudioCodecNotSupported" in reasons:
                    reason_detail.append(
                        f"Audio: File has {file_audio['codec'].upper()} {file_audio['title']} "
                        f"({file_audio['channels']}ch). Roku profile limits audio to 2 channels "
                        f"for AAC/MP3. DTS-X 7.1 is not passthrough-supported."
                    )
                if "VideoBitrateNotSupported" in reasons:
                    reason_detail.append(
                        f"Bitrate: File is {file_video['bitrate_mbps']} Mbps "
                        f"({file_video['codec'].upper()} {file_video['profile']}). "
                        f"Roku H264 profile cap is 10 Mbps. HEVC cap is 40 Mbps."
                    )
                if "VideoRangeTypeNotSupported" in reasons:
                    reason_detail.append(
                        f"HDR: File is {file_video['hdr_type']}. "
                        f"Roku H264 profile only allows SDR|DOVIWithSDR. "
                        f"HEVC profile supports HDR10."
                    )

                # What would allow direct play
                direct_play_options = []
                if "VideoRangeTypeNotSupported" in reasons or "VideoBitrateNotSupported" in reasons:
                    direct_play_options.append(
                        "Switch Jellyfin to use HEVC codec for this client - "
                        "Roku supports HEVC up to 40 Mbps with HDR10, which matches this file."
                    )
                if "AudioCodecNotSupported" in reasons:
                    direct_play_options.append(
                        f"File has AC3 DD 5.1 track (index 2) which the Roku DOES support for direct play. "
                        f"Set default audio track to AC3 5.1 instead of DTS-X in Jellyfin user settings."
                    )
                    direct_play_options.append(
                        "In Jellyfin Dashboard > Playback > check 'Enable audio passthrough' "
                        "and add AC3/EAC3 to preferred audio codecs for Roku profile."
                    )

                out["active_transcodes"].append({
                    **entry,
                    "transcode_reasons":    reasons,
                    "reason_detail":        reason_detail,
                    "file_video":           file_video,
                    "file_audio":           file_audio,
                    "transcoding_to":       transcode_to,
                    "client_limits":        client_limits,
                    "direct_play_options":  direct_play_options,
                    "hw_accel_type":        ti.get("HardwareAccelerationType"),
                    "hw_accel_active":      not ti.get("IsVideoDirect", True),
                })

    # System info
    info, err = safe_get(f"{base}/System/Info", headers=h)
    if err:
        out["errors"].append({"source": "system_info", "error": err})
    else:
        out["system_info"] = {
            "version":          info.get("Version"),
            "server_name":      info.get("ServerName"),
            "operating_system": info.get("OperatingSystem"),
            "has_update":       info.get("HasUpdateAvailable"),
        }

    # Playback reporting - recent activity via /Users/{userId}/Items with Filters=IsUnplayed
    # We use the activity log endpoint
    activity, err = safe_get(f"{base}/System/ActivityLog/Entries?limit=50", headers=h)
    if err:
        out["errors"].append({"source": "activity_log", "error": err})
    else:
        playback_errors = []
        for item in (activity or {}).get("Items", []):
            severity = item.get("Severity", "")
            if severity in ("Error", "Warning"):
                playback_errors.append({
                    "date":     item.get("Date"),
                    "name":     item.get("Name"),
                    "severity": severity,
                    "overview": item.get("Overview"),
                    "type":     item.get("Type"),
                })
        out["recent_errors"] = playback_errors

    return out


# -- Sonarr --------------------------------------------------------------------

def collect_sonarr(cfg):
    base = cfg["services"]["sonarr"]["url"]
    key  = cfg["services"]["sonarr"]["api_key"]
    h    = {"X-Api-Key": key}
    out  = {"service": "sonarr", "errors": []}

    # Wanted / missing episodes
    wanted, err = safe_get(f"{base}/api/v3/wanted/missing?pageSize=50&sortKey=airDateUtc&sortDir=desc&includeSeries=true", headers=h)
    if err:
        out["errors"].append({"source": "wanted_missing", "error": err})
        out["missing_episodes"] = []
    else:
        out["missing_episodes"] = [
            {
                "series":    ep.get("series", {}).get("title") or ep.get("seriesTitle", "Unknown Series"),
                "season":    ep.get("seasonNumber"),
                "episode":   ep.get("episodeNumber"),
                "title":     ep.get("title"),
                "air_date":  ep.get("airDateUtc"),
            }
            for ep in (wanted or {}).get("records", [])
        ]

    # Queue (current downloads)
    queue, err = safe_get(f"{base}/api/v3/queue?pageSize=50", headers=h)
    if err:
        out["errors"].append({"source": "queue", "error": err})
        out["download_queue"] = []
    else:
        out["download_queue"] = [
            {
                "series":    q.get("series", {}).get("title"),
                "episode":   q.get("episode", {}).get("title"),
                "status":    q.get("status"),
                "error":     q.get("errorMessage"),
                "sizeleft":  q.get("sizeleft"),
                "eta":       q.get("estimatedCompletionTime"),
            }
            for q in (queue or {}).get("records", [])
        ]

    # Health checks
    health, err = safe_get(f"{base}/api/v3/health", headers=h)
    if err:
        out["errors"].append({"source": "health", "error": err})
        out["health_issues"] = []
    else:
        out["health_issues"] = [
            {"type": h2.get("type"), "message": h2.get("message"), "wiki": h2.get("wikiUrl")}
            for h2 in (health or [])
        ]

    # Disk space
    disk, err = safe_get(f"{base}/api/v3/diskspace", headers=h)
    if err:
        out["errors"].append({"source": "diskspace", "error": err})
    else:
        out["disk_space"] = [
            {
                "path":       d.get("path"),
                "free_gb":    round(d.get("freeSpace", 0) / 1e9, 2),
                "total_gb":   round(d.get("totalSpace", 1) / 1e9, 2),
                "used_pct":   round(100 * (1 - d.get("freeSpace", 0) / max(d.get("totalSpace", 1), 1)), 1),
            }
            for d in (disk or [])
        ]

    return out


# -- Radarr --------------------------------------------------------------------

def collect_radarr(cfg):
    base = cfg["services"]["radarr"]["url"]
    key  = cfg["services"]["radarr"]["api_key"]
    h    = {"X-Api-Key": key}
    out  = {"service": "radarr", "errors": []}

    # Missing / not available movies
    movies, err = safe_get(f"{base}/api/v3/movie", headers=h)
    if err:
        out["errors"].append({"source": "movies", "error": err})
        out["missing_movies"] = []
    else:
        out["missing_movies"] = [
            {
                "title":     m.get("title"),
                "year":      m.get("year"),
                "status":    m.get("status"),
                "monitored": m.get("monitored"),
            }
            for m in (movies or [])
            if m.get("monitored") and not m.get("hasFile")
        ]

    # Queue
    queue, err = safe_get(f"{base}/api/v3/queue?pageSize=50", headers=h)
    if err:
        out["errors"].append({"source": "queue", "error": err})
        out["download_queue"] = []
    else:
        out["download_queue"] = [
            {
                "movie":     q.get("movie", {}).get("title"),
                "status":    q.get("status"),
                "error":     q.get("errorMessage"),
                "sizeleft":  q.get("sizeleft"),
                "eta":       q.get("estimatedCompletionTime"),
            }
            for q in (queue or {}).get("records", [])
        ]

    # Health
    health, err = safe_get(f"{base}/api/v3/health", headers=h)
    if err:
        out["errors"].append({"source": "health", "error": err})
        out["health_issues"] = []
    else:
        out["health_issues"] = [
            {"type": h2.get("type"), "message": h2.get("message")}
            for h2 in (health or [])
        ]

    # Disk space
    disk, err = safe_get(f"{base}/api/v3/diskspace", headers=h)
    if err:
        out["errors"].append({"source": "diskspace", "error": err})
    else:
        out["disk_space"] = [
            {
                "path":     d.get("path"),
                "free_gb":  round(d.get("freeSpace", 0) / 1e9, 2),
                "total_gb": round(d.get("totalSpace", 1) / 1e9, 2),
                "used_pct": round(100 * (1 - d.get("freeSpace", 0) / max(d.get("totalSpace", 1), 1)), 1),
            }
            for d in (disk or [])
        ]

    return out


# -- Jellyseer -----------------------------------------------------------------

def collect_jellyseer(cfg):
    base = cfg["services"]["jellyseer"]["url"]
    key  = cfg["services"]["jellyseer"]["api_key"]
    h    = {"X-Api-Key": key}
    out  = {"service": "jellyseer", "errors": [], "connected": False}

    statuses = {
        1: "PENDING",
        2: "APPROVED",
        3: "DECLINED",
        4: "AVAILABLE",
        5: "PROCESSING",
        8: "FAILED"
    }

    # Pull all requests
    requests_data, err = safe_get(
        f"{base}/api/v1/request?take=100&skip=0&sort=added",
        headers=h
    )
    if err:
        out["errors"].append({"source": "requests", "error": err})
        out["all_requests"]   = []
        out["not_in_library"] = []
        out["total_requests"] = 0
        return out

    out["connected"] = True
    all_results = (requests_data or {}).get("results", [])
    out["total_requests"] = (requests_data or {}).get("pageInfo", {}).get("results", len(all_results))

    def slug_to_title(slug):
        """Convert externalServiceSlug like harry-potter to Harry Potter.
        Returns None if slug is just a number (movies use tmdbId as slug)."""
        if not slug:
            return None
        if slug.isdigit():
            return None  # numeric slug = tmdbId, not a real title
        return " ".join(word.capitalize() for word in slug.replace("-", " ").split())

    def get_title_from_tmdb(media_type, tmdb_id):
        """Look up title directly from Jellyseer media details endpoint."""
        endpoint = "movie" if media_type == "movie" else "tv"
        data, err = safe_get(f"{base}/api/v1/{endpoint}/{tmdb_id}", headers=h)
        if err or not data:
            return None, None
        title = (
            data.get("title") or
            data.get("name") or
            data.get("originalTitle") or
            data.get("originalName")
        )
        date_str = data.get("releaseDate") or data.get("firstAirDate") or ""
        year = date_str[:4] if date_str else ""
        return title, year

    all_requests = []
    not_in_library = []

    for r in all_results:
        media      = r.get("media", {})
        status     = statuses.get(r.get("status"), "UNKNOWN")
        media_type = r.get("type", "movie")
        tmdb_id    = media.get("tmdbId")
        slug       = media.get("externalServiceSlug") or media.get("externalServiceSlug4k")

        # Try slug first (instant, no extra call), then TMDB lookup, then fallback
        slug_title = slug_to_title(slug)
        if slug_title:
            title, year = slug_title, ""
        elif tmdb_id:
            title, year = get_title_from_tmdb(media_type, tmdb_id)
            if not title:
                title = f"tmdbId:{tmdb_id}"
                year  = ""
        else:
            title, year = "Unknown", ""

        entry = {
            "title":        title,
            "year":         year,
            "type":         media_type,
            "status":       status,
            "media_status": media.get("status"),
            "requested_by": r.get("requestedBy", {}).get("displayName", "unknown"),
            "created":      r.get("createdAt", "")[:10],
            "tmdb_id":      tmdb_id,
        }

        all_requests.append(entry)

        # Not in library = media status not AVAILABLE (5)
        if media.get("status") not in (5,):
            not_in_library.append(entry)

    out["all_requests"]   = all_requests
    out["not_in_library"] = not_in_library

    return out


# -- Tautulli ------------------------------------------------------------------

def collect_tautulli(cfg):
    base = cfg["services"]["tautulli"]["url"]
    key  = cfg["services"]["tautulli"]["api_key"]
    out  = {"service": "tautulli", "errors": []}

    def t_get(cmd, **params):
        p = {"apikey": key, "cmd": cmd, **params}
        return safe_get(f"{base}/api/v2", headers=None)  # tautulli uses query params
    
    def tautulli_api(cmd, **params):
        try:
            p = {"apikey": key, "cmd": cmd, **params}
            r = requests.get(f"{base}/api/v2", params=p, timeout=8)
            r.raise_for_status()
            data = r.json()
            return data.get("response", {}).get("data"), None
        except Exception as e:
            return None, str(e)

    # Current activity
    activity, err = tautulli_api("get_activity")
    if err:
        out["errors"].append({"source": "activity", "error": err})
        out["current_streams"] = []
    else:
        out["stream_count"]   = (activity or {}).get("stream_count", 0)
        out["transcode_count"] = (activity or {}).get("stream_count_transcode", 0)
        out["direct_count"]   = (activity or {}).get("stream_count_direct_play", 0)
        out["current_streams"] = [
            {
                "user":           s.get("friendly_name"),
                "title":          s.get("full_title"),
                "media_type":     s.get("media_type"),
                "play_method":    s.get("transcode_decision"),
                "player":         s.get("player"),
                "platform":       s.get("platform"),
                "quality":        s.get("quality"),
                "transcode_hw":   s.get("transcode_hw_decoding"),
                "progress_pct":   s.get("progress_percent"),
                "bandwidth_kbps": s.get("bandwidth"),
            }
            for s in (activity or {}).get("sessions", [])
        ]

    # Recent history (last 24h) for playback errors
    history, err = tautulli_api("get_history", length=50)
    if err:
        out["errors"].append({"source": "history", "error": err})
        out["recent_history"] = []
    else:
        out["recent_history"] = [
            {
                "date":        h2.get("date"),
                "user":        h2.get("friendly_name"),
                "title":       h2.get("full_title"),
                "play_method": h2.get("transcode_decision"),
                "stopped":     h2.get("stopped"),
                "paused_ctr":  h2.get("paused_counter"),
                "watched_pct": h2.get("watched_status"),
            }
            for h2 in (history or {}).get("data", [])
        ]

    # Notification logs (errors)
    notif, err = tautulli_api("get_notification_log")
    if err:
        out["errors"].append({"source": "notifications", "error": err})
    else:
        out["notification_errors"] = [
            {"timestamp": n.get("timestamp"), "agent": n.get("agent_name"), "msg": n.get("subject_text")}
            for n in (notif or {}).get("data", [])
            if n.get("success") == 0
        ]

    return out


# -- System metrics ------------------------------------------------------------

def collect_system():
    out = {"service": "system"}
    try:
        import shutil, psutil
        out["cpu_percent"]    = psutil.cpu_percent(interval=1)
        out["memory_percent"] = psutil.virtual_memory().percent
        out["memory_used_gb"] = round(psutil.virtual_memory().used / 1e9, 2)
        out["memory_total_gb"]= round(psutil.virtual_memory().total / 1e9, 2)
        disks = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "mountpoint": part.mountpoint,
                    "total_gb":   round(usage.total / 1e9, 2),
                    "used_gb":    round(usage.used / 1e9, 2),
                    "free_gb":    round(usage.free / 1e9, 2),
                    "used_pct":   usage.percent,
                })
            except PermissionError:
                pass
        out["disks"] = disks
    except ImportError:
        out["error"] = "psutil not installed - run: pip install psutil"
    return out


# -- Entry point ---------------------------------------------------------------

def collect_smart():
    """Read SMART data from smart_cache.json written by collect_smart.py."""
    cache_file = Path(__file__).parent.parent / "logs" / "smart_cache.json"
    if not cache_file.exists():
        return {"error": "smart_cache.json not found - run collect_smart.py first as Admin", "drives": []}
    try:
        with open(cache_file, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {"error": "smart_cache.json is empty", "drives": []}
        drives = json.loads(content)
        return {"drives": drives}
    except Exception as e:
        return {"error": str(e), "drives": []}


def collect_recommendations(cfg):
    """Run collect_recommendations.py and return results."""
    rec_script = Path(__file__).parent / "collect_recommendations.py"
    if not rec_script.exists():
        return {"status": "missing", "recommendations": []}
    try:
        result = subprocess.run(
            [sys.executable, str(rec_script)],
            capture_output=True, text=True, timeout=180
        )
        if result.stdout.strip():
            return json.loads(result.stdout.strip())
        return {"status": "error", "error": result.stderr.strip()[:300], "recommendations": []}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "recommendations": []}
    except Exception as e:
        return {"status": "error", "error": str(e), "recommendations": []}


def collect_playback_errors_safe(cfg=None):
    """
    Load and run collect_playback_errors.py from the same scripts directory.
    Returns a safe fallback dict on any error.
    """
    try:
        script_path = Path(__file__).parent / "collect_playback_errors.py"
        if not script_path.exists():
            return {
                "incidents": [], "total_incidents": 0,
                "error": f"collect_playback_errors.py not found at {script_path}"
            }
        spec = importlib.util.spec_from_file_location("collect_playback_errors", script_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.collect_playback_errors(cfg)
    except Exception as e:
        sys.stderr.write(f"[collect_data] playback_errors collection failed: {e}\n")
        return {"incidents": [], "total_incidents": 0, "error": str(e)}


def collect_import_failures_safe(cfg):
    """
    Load and run collect_import_failures.py from the same scripts directory.
    Returns a safe fallback dict on any error so the rest of the report
    is never blocked by import-failure collection issues.
    """
    try:
        script_path = Path(__file__).parent / "collect_import_failures.py"
        if not script_path.exists():
            return {
                "sonarr": [], "radarr": [], "total_failures": 0,
                "error": f"collect_import_failures.py not found at {script_path}"
            }
        spec = importlib.util.spec_from_file_location("collect_import_failures", script_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.collect_import_failures(cfg)
    except Exception as e:
        sys.stderr.write(f"[collect_data] import_failures collection failed: {e}\n")
        return {"sonarr": [], "radarr": [], "total_failures": 0, "error": str(e)}


def main():
    cfg = load_config()
    thresholds = cfg.get("thresholds", {})
    payload = {
        "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "server_ip":    cfg["server"]["ip"],
        "config_thresholds": {
            "disk_free_good_gb":     thresholds.get("disk_free_good_gb",     2000),
            "disk_free_warning_gb":  thresholds.get("disk_free_warning_gb",  1500),
            "disk_free_critical_gb": thresholds.get("disk_free_critical_gb", 1000),
            "monitored_drives":      thresholds.get("monitored_drives",      ["K:\\"]),
            "drivepool_drive":       thresholds.get("drivepool_drive",       "K:\\"),
        },
        "services": [
            collect_jellyfin(cfg),
            collect_sonarr(cfg),
            collect_radarr(cfg),
            collect_jellyseer(cfg),
        ],
        "system":        collect_system(),
        "smart_health":  collect_smart(),
        "recommendations": collect_recommendations(cfg),
        # --- Import failure detection ---
        "import_failures": collect_import_failures_safe(cfg),
        # --- Playback error detection ---
        "playback_errors": collect_playback_errors_safe(cfg),
        # Config snapshot so renderers know arr URLs without re-reading config
        "_config_snapshot": {
            "sonarr_url": cfg["services"]["sonarr"]["url"],
            "radarr_url": cfg["services"]["radarr"]["url"],
        },
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
