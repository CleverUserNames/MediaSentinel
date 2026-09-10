"""
collect_import_failures.py
MediaSentinel - Sonarr/Radarr import failure detector

Checks two sources per arr:
  1. /api/v3/queue   - downloads that completed but are stuck (importPending / importFailed)
  2. /api/v3/history - recent events that show an importFailed outcome

Output: JSON dict with keys "sonarr" and "radarr", each a list of failure dicts.
Called from collect_data.py and merged into the main data_*.json payload.
"""

import json
import sys
import os
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def age_str(added_str):
    """Return a human-readable age like '2h 14m' from an ISO timestamp string."""
    if not added_str:
        return "unknown"
    try:
        # Sonarr/Radarr return UTC timestamps like 2026-09-04T12:00:00Z
        added_str = added_str.rstrip("Z")
        if "." in added_str:
            added_str = added_str[:added_str.index(".")]
        dt = datetime.fromisoformat(added_str).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        total_minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        days, hours = divmod(hours, 24)
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "unknown"


def age_minutes(added_str):
    """Return age in minutes as an integer. Returns 9999 on parse failure."""
    if not added_str:
        return 9999
    try:
        added_str = added_str.rstrip("Z")
        if "." in added_str:
            added_str = added_str[:added_str.index(".")]
        dt = datetime.fromisoformat(added_str).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return int(delta.total_seconds() // 60)
    except Exception:
        return 9999


def arr_get(base_url, api_key, path, params=None):
    """Simple GET against an arr API. Returns parsed JSON or None on error."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = requests.get(
            url,
            params=params or {},
            headers={"X-Api-Key": api_key},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        sys.stderr.write(f"[import_failures] GET {url} failed: {e}\n")
        return None


# ---------------------------------------------------------------------------
# Queue - stuck downloads
# ---------------------------------------------------------------------------
# trackedDownloadState values that mean "download finished but import didn't":
STUCK_STATES = {"importPending", "importFailed"}

# trackedDownloadStatus values worth surfacing even if state looks OK:
WARN_STATUSES = {"warning", "error"}

# Minimum age before we flag something (avoids false positives on fresh grabs
# that haven't had time for Sonarr/Radarr to attempt an import yet)
GRACE_PERIOD_MINUTES = 30


def get_queue_failures(base_url, api_key, arr_name):
    """
    Pull the arr queue and return items that are download-complete but
    stuck on import. Each returned dict has:
      title, type, output_path, state, status, messages, age_str, age_minutes, source
    """
    data = arr_get(base_url, api_key, "api/v3/queue", {
        "pageSize": 200,
        "includeUnknownSeriesItems": True,   # Sonarr
        "includeUnknownMovieItems": True,    # Radarr
        "includeSeries": True,               # Sonarr - attach series info
        "includeMovie": True,                # Radarr - attach movie info
    })
    if not data:
        return []

    records = data.get("records", [])
    failures = []

    for item in records:
        download_status = item.get("status", "")          # "downloading", "completed", etc.
        tracked_state  = item.get("trackedDownloadState", "")   # "importPending", "importFailed", etc.
        tracked_status = item.get("trackedDownloadStatus", "ok") # "ok", "warning", "error"

        # Only care about downloads that are "completed" at the download-client level
        if download_status != "completed":
            continue

        is_stuck = (
            tracked_state in STUCK_STATES
            or tracked_status in WARN_STATUSES
        )
        if not is_stuck:
            continue

        added = item.get("added", "")
        mins  = age_minutes(added)

        # Skip anything younger than the grace period
        if mins < GRACE_PERIOD_MINUTES:
            continue

        # Build readable message list
        status_messages = []
        for msg_block in item.get("statusMessages", []):
            title = msg_block.get("title", "")
            for msg in msg_block.get("messages", []):
                status_messages.append(msg if msg else title)
            if not msg_block.get("messages") and title:
                status_messages.append(title)

        if not status_messages:
            status_messages = ["No specific reason provided by arr"]

        # Try to get a clean display title
        title = item.get("title", "Unknown")
        series = item.get("series")
        movie  = item.get("movie")
        if series and series.get("title"):
            ep_title = title
            s_title  = series["title"]
            title    = f"{s_title}: {ep_title}" if ep_title != s_title else s_title
        elif movie and movie.get("title"):
            title = movie["title"]

        failures.append({
            "title":        title,
            "arr":          arr_name,
            "source":       "queue",
            "download_state":  tracked_state,
            "download_status": tracked_status,
            "output_path":  item.get("outputPath", ""),
            "download_client": item.get("downloadClient", ""),
            "messages":     status_messages,
            "age_str":      age_str(added),
            "age_minutes":  mins,
            "queue_id":     item.get("id"),          # used for retry command
            "series_id":    (series or {}).get("id"),
            "movie_id":     (movie or {}).get("id"),
        })

    return failures


# ---------------------------------------------------------------------------
# History - past import failures (fell out of queue without successful import)
# ---------------------------------------------------------------------------
# Sonarr/Radarr history eventType values:
#   1 = grabbed, 2 = downloadFolderImported, 3 = downloadFailed,
#   4 = episodeFileDeleted / movieFileDeleted, 5 = episodeFileRenamed,
#   7 = downloadIgnored
# "importFailed" shows as eventType=3 in practice; we scan last 48h.

HISTORY_HOURS = 48
IMPORT_FAILED_EVENTS = {"downloadFailed", "importFailed"}


def get_history_failures(base_url, api_key, arr_name):
    """
    Pull recent arr history and return items whose last event was an import
    failure (not superseded by a successful import).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HISTORY_HOURS)

    # Fetch grabbed events and failed events together; we need both to correlate
    failures_raw = []
    successes    = set()   # download IDs that eventually succeeded

    page = 1
    while True:
        data = arr_get(base_url, api_key, "api/v3/history", {
            "pageSize":  100,
            "page":      page,
            "sortKey":   "date",
            "sortDir":   "desc",
        })
        if not data:
            break

        records = data.get("records", [])
        if not records:
            break

        for rec in records:
            # Parse date
            date_str = rec.get("date", "")
            mins     = age_minutes(date_str)
            if mins > HISTORY_HOURS * 60:
                # Older than our window - stop paginating
                break

            event = rec.get("eventType", "")

            if event == "downloadFolderImported":
                # This download ID succeeded - exclude it from failures
                dl_id = rec.get("downloadId", "")
                if dl_id:
                    successes.add(dl_id)

            elif event in IMPORT_FAILED_EVENTS or (
                event == "downloadFailed" and
                "import" in str(rec.get("data", {}).get("message", "")).lower()
            ):
                # Build message from data blob
                data_blob = rec.get("data", {})
                msg = (
                    data_blob.get("message")
                    or data_blob.get("importFailedMessage")
                    or "Import failed"
                )

                # Title
                title = rec.get("sourceTitle", "Unknown")
                series = rec.get("series", {}) or {}
                movie  = rec.get("movie",  {}) or {}
                if series.get("title"):
                    title = series["title"]
                elif movie.get("title"):
                    title = movie["title"]

                failures_raw.append({
                    "title":       title,
                    "arr":         arr_name,
                    "source":      "history",
                    "download_id": rec.get("downloadId", ""),
                    "output_path": data_blob.get("path", ""),
                    "messages":    [msg],
                    "age_str":     age_str(date_str),
                    "age_minutes": mins,
                    "series_id":   series.get("id"),
                    "movie_id":    movie.get("id"),
                    "download_state":  "importFailed",
                    "download_status": "error",
                    "download_client": data_blob.get("downloadClient", ""),
                    "queue_id":    None,
                })
        else:
            page += 1
            continue
        break  # inner loop broke early - stop paginating

    # Remove any that later had a successful import
    return [f for f in failures_raw if f["download_id"] not in successes]


# ---------------------------------------------------------------------------
# Dedup queue + history results
# ---------------------------------------------------------------------------

def merge_and_dedup(queue_items, history_items):
    """
    Queue items take priority. Suppress history items whose title already
    appears in queue results (same stuck item showing up in both).
    """
    seen_titles = {item["title"] for item in queue_items}
    deduped_history = [h for h in history_items if h["title"] not in seen_titles]
    return queue_items + deduped_history


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_import_failures(config):
    cfg_sonarr = config["services"]["sonarr"]
    cfg_radarr = config["services"]["radarr"]

    results = {
        "sonarr": [],
        "radarr": [],
        "total_failures": 0,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    # --- Sonarr ---
    sonarr_queue   = get_queue_failures(cfg_sonarr["url"], cfg_sonarr["api_key"], "Sonarr")
    sonarr_history = get_history_failures(cfg_sonarr["url"], cfg_sonarr["api_key"], "Sonarr")
    results["sonarr"] = merge_and_dedup(sonarr_queue, sonarr_history)

    # --- Radarr ---
    radarr_queue   = get_queue_failures(cfg_radarr["url"], cfg_radarr["api_key"], "Radarr")
    radarr_history = get_history_failures(cfg_radarr["url"], cfg_radarr["api_key"], "Radarr")
    results["radarr"] = merge_and_dedup(radarr_queue, radarr_history)

    results["total_failures"] = len(results["sonarr"]) + len(results["radarr"])
    return results


if __name__ == "__main__":
    cfg  = load_config()
    data = collect_import_failures(cfg)
    print(json.dumps(data, indent=2))
