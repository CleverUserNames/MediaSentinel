#!/usr/bin/env python3
"""
fix_executor.py  (Windows edition)
Translates AI-recommended fix_steps into safe API calls against the
media stack. Runs in dry-run mode by default.
Pass --execute to actually apply fixes.
"""

import argparse
import json
import sys
import os
import requests
import subprocess
from pathlib import Path
from datetime import datetime

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def log(msg, level="INFO"):
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr)


class FixExecutor:
    def __init__(self, cfg, dry_run=True):
        self.cfg     = cfg
        self.dry_run = dry_run
        self.results = []

    def _api(self, service, method, path, body=None):
        svc  = self.cfg["services"][service]
        base = svc["url"]
        key  = svc["api_key"]
        if service == "jellyfin":
            headers = {
                "X-Emby-Authorization": f'MediaBrowser Token="{key}"',
                "Content-Type": "application/json"
            }
        else:
            headers = {"X-Api-Key": key, "Content-Type": "application/json"}

        url = f"{base}{path}"
        if self.dry_run:
            log(f"[DRY-RUN] {method.upper()} {url} body={json.dumps(body or {})[:200]}")
            return {"dry_run": True}, None
        try:
            r = getattr(requests, method)(url, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            return r.json() if r.text else {}, None
        except Exception as e:
            return None, str(e)

    # ── Sonarr ────────────────────────────────────────────────────────

    def sonarr_search_missing(self, series_id=None):
        body = {"name": "SeriesSearch", "seriesId": series_id} if series_id else {"name": "MissingEpisodeSearch"}
        _, err = self._api("sonarr", "post", "/api/v3/command", body)
        self._record("sonarr_search_missing", err is None, err)

    def sonarr_refresh_series(self, series_id=None):
        body = {"name": "RefreshSeries"}
        if series_id:
            body["seriesId"] = series_id
        _, err = self._api("sonarr", "post", "/api/v3/command", body)
        self._record("sonarr_refresh_series", err is None, err)

    def sonarr_rescan_series(self, series_id=None):
        body = {"name": "RescanSeries"}
        if series_id:
            body["seriesId"] = series_id
        _, err = self._api("sonarr", "post", "/api/v3/command", body)
        self._record("sonarr_rescan_series", err is None, err)

    # ── Radarr ────────────────────────────────────────────────────────

    def radarr_search_missing(self):
        _, err = self._api("radarr", "post", "/api/v3/command", {"name": "MissingMoviesSearch"})
        self._record("radarr_search_missing", err is None, err)

    def radarr_refresh_movies(self):
        _, err = self._api("radarr", "post", "/api/v3/command", {"name": "RefreshMovie"})
        self._record("radarr_refresh_movies", err is None, err)

    def radarr_rescan_movie(self, movie_id):
        _, err = self._api("radarr", "post", "/api/v3/command", {"name": "RescanMovie", "movieId": movie_id})
        self._record("radarr_rescan_movie", err is None, err)

    # ── Jellyfin ──────────────────────────────────────────────────────

    def jellyfin_scan_library(self):
        _, err = self._api("jellyfin", "post", "/Library/Refresh")
        self._record("jellyfin_scan_library", err is None, err)

    def jellyfin_stop_transcode(self, play_session_id):
        _, err = self._api("jellyfin", "delete", f"/Videos/ActiveEncodings?deviceId={play_session_id}")
        self._record("jellyfin_stop_transcode", err is None, err)

    # ── Windows system fixes ──────────────────────────────────────────

    def clear_jellyfin_transcode_cache(self):
        """
        Clear Jellyfin transcode temp files on Windows.
        Default path is inside ProgramData — adjust if your install differs.
        """
        transcode_path = self.cfg.get("paths", {}).get(
            "jellyfin_transcode",
            r"C:\ProgramData\Jellyfin\Server\transcodes"
        )
        if self.dry_run:
            log(f"[DRY-RUN] Would delete files in: {transcode_path}")
            self._record("clear_transcode_cache", True, None)
            return
        try:
            deleted = 0
            p = Path(transcode_path)
            if p.exists():
                for f in p.glob("*"):
                    if f.is_file():
                        f.unlink()
                        deleted += 1
            log(f"Deleted {deleted} transcode cache files from {transcode_path}")
            self._record("clear_transcode_cache", True, None)
        except Exception as e:
            self._record("clear_transcode_cache", False, str(e))

    def free_disk_space_report(self):
        """Use PowerShell to report largest folders on the system drive."""
        system_drive = self.cfg.get("paths", {}).get("system_drive", "C:\\")
        ps_cmd = (
            f"Get-ChildItem {system_drive} -ErrorAction SilentlyContinue | "
            "ForEach-Object { $s = (Get-ChildItem $_.FullName -Recurse -ErrorAction SilentlyContinue | "
            "Measure-Object -Property Length -Sum).Sum; "
            "[PSCustomObject]@{Path=$_.FullName; SizeGB=[math]::Round($s/1GB,2)} } | "
            "Sort-Object SizeGB -Descending | Select-Object -First 15 | Format-Table -AutoSize"
        )
        if self.dry_run:
            log(f"[DRY-RUN] Would run disk space report via PowerShell")
            self._record("free_disk_space_report", True, None)
            return
        try:
            result = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                timeout=60, stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="replace")
            log(f"Disk usage report:\n{result}")
            self._record("free_disk_space_report", True, None)
        except Exception as e:
            self._record("free_disk_space_report", False, str(e))

    def restart_jellyfin_service(self):
        """Restart the Jellyfin Windows service."""
        if self.dry_run:
            log("[DRY-RUN] Would run: Restart-Service JellyfinServer")
            self._record("restart_jellyfin_service", True, None)
            return
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Restart-Service JellyfinServer -Force"],
                check=True, timeout=60
            )
            self._record("restart_jellyfin_service", True, None)
        except Exception as e:
            self._record("restart_jellyfin_service", False, str(e))

    # ── Orchestration ─────────────────────────────────────────────────

    def apply_from_analysis(self, analysis: dict):
        issues    = analysis.get("issues", [])
        automated = [i for i in issues if i.get("fix_type") == "automated"]
        log(f"Found {len(issues)} total issues, {len(automated)} automated-fixable")

        for issue in automated:
            category = issue.get("category")
            service  = issue.get("service")
            severity = issue.get("severity", "INFO")
            title    = issue.get("title", "")
            log(f"Processing [{severity}] {title} ({service}/{category})")

            if service == "sonarr" and category == "missing_file":
                self.sonarr_search_missing()
            elif service == "sonarr" and category == "config":
                self.sonarr_refresh_series()
            elif service == "radarr" and category == "missing_file":
                self.radarr_search_missing()
            elif service == "radarr" and category == "config":
                self.radarr_refresh_movies()
            elif service == "jellyfin" and category == "missing_file":
                self.jellyfin_scan_library()
            elif service == "jellyfin" and category == "transcode":
                self.clear_jellyfin_transcode_cache()
            elif category == "disk":
                self.free_disk_space_report()
            else:
                log(f"  No automated handler for {service}/{category} - manual review needed")
                self._record(f"manual_{issue.get('id','unknown')}", None, "Requires manual fix")

        return self.results

    def _record(self, action, success, error):
        self.results.append({
            "action":  action,
            "success": success,
            "error":   error,
            "dry_run": self.dry_run,
        })


def main():
    parser = argparse.ArgumentParser(description="MediaSentinel fix executor (Windows)")
    parser.add_argument("--execute",  action="store_true", help="Apply fixes for real (default: dry-run)")
    parser.add_argument("--analysis", help="Path to analysis JSON file")
    parser.add_argument("--action",   help="Run a specific fix action directly")
    args = parser.parse_args()

    cfg      = load_config()
    executor = FixExecutor(cfg, dry_run=not args.execute)

    if args.action:
        fn = getattr(executor, args.action, None)
        if fn:
            fn()
        else:
            print(f"Unknown action: {args.action}", file=sys.stderr)
            sys.exit(1)
    elif args.analysis:
        with open(args.analysis, encoding="utf-8") as f:
            analysis = json.load(f)
        executor.apply_from_analysis(analysis)
    else:
        analysis = json.load(sys.stdin)
        executor.apply_from_analysis(analysis)

    print(json.dumps(executor.results, indent=2))


if __name__ == "__main__":
    main()
