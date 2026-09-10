# -*- coding: utf-8 -*-
"""
render_playback_errors.py
MediaSentinel - Playback errors HTML section renderer

Called from render_report.py to produce the Playback Errors section.
Standalone test:
    python render_playback_errors.py logs\data_<timestamp>.json
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sev_color(sev):
    return {"CRITICAL": "#ef4444", "WARNING": "#f59e0b", "INFO": "#3b82f6"}.get(sev, "#6b7280")

def _sev_label(sev):
    return {"CRITICAL": "[!] CRITICAL", "WARNING": "[W] WARNING", "INFO": "[i] INFO"}.get(sev, sev)

def _fmt_time(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except Exception:
        return iso_str[:16]

def _safe(text):
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", errors="replace").decode("ascii").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

SECTION_CSS = """
/* ---- Playback Errors section ---- */
.playback-errors-section { margin: 24px 0; }

.pe-card {
  background: #1e1e2e;
  border-radius: 10px;
  border-left: 5px solid #555;
  padding: 16px 20px;
  margin-bottom: 14px;
}
.pe-card.sev-CRITICAL { border-left-color: #ef4444; }
.pe-card.sev-WARNING  { border-left-color: #f59e0b; }
.pe-card.sev-INFO     { border-left-color: #3b82f6; }

.pe-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.pe-title {
  font-size: 1.0em;
  font-weight: 600;
  color: #e8e8f0;
}
.pe-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.pe-badge {
  font-size: 0.72em;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 20px;
  color: #fff;
}
.pe-meta {
  font-size: 0.72em;
  padding: 2px 8px;
  border-radius: 20px;
  background: #2d2d42;
  color: #aaa;
}

.pe-media {
  background: #15151f;
  border-radius: 6px;
  padding: 6px 12px;
  margin-bottom: 10px;
  font-size: 0.83em;
  word-break: break-all;
}
.pe-media-label { color: #888; margin-right: 6px; }
.pe-media code  { color: #9ecbff; }

.pe-cause {
  font-size: 0.87em;
  color: #b8b8cc;
  margin-bottom: 10px;
  line-height: 1.5;
}
.pe-cause-label { color: #e0c87a; font-weight: 600; margin-right: 4px; }

.pe-fix {
  background: #0f1f0f;
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 10px;
}
.pe-fix-label {
  font-size: 0.75em;
  font-weight: 700;
  color: #4ade80;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}
.pe-fix ol {
  margin: 0;
  padding-left: 1.3em;
}
.pe-fix li {
  font-size: 0.84em;
  color: #c8e6c9;
  margin-bottom: 4px;
  line-height: 1.5;
}
.pe-fix code {
  background: #1a2e1a;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.92em;
  color: #a5d6a7;
  word-break: break-all;
}

.pe-ffmpeg {
  margin-top: 8px;
  background: #1a1a2e;
  border-radius: 6px;
  padding: 8px 12px;
}
.pe-ffmpeg-label {
  font-size: 0.75em;
  color: #7986cb;
  font-weight: 600;
  margin-bottom: 4px;
}
.pe-ffmpeg-err {
  font-size: 0.78em;
  color: #9fa8da;
  font-family: monospace;
  margin-bottom: 2px;
  word-break: break-all;
}

.pe-empty {
  color: #6a6a80;
  font-style: italic;
  padding: 10px 0;
}
.pe-summary {
  font-size: 0.85em;
  color: #888;
  margin-bottom: 14px;
}
.pe-ffmpeg-stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.pe-stat-pill {
  background: #1e1e2e;
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 0.8em;
  color: #aaa;
}
.pe-stat-pill strong { color: #e8e8f0; }
"""


def get_css():
    return SECTION_CSS


# ---------------------------------------------------------------------------
# Card renderer
# ---------------------------------------------------------------------------

def _render_card(incident):
    sev         = incident.get("severity", "INFO")
    sc          = _sev_color(sev)
    title       = _safe(incident.get("title", "Unknown"))
    cause       = _safe(incident.get("cause", ""))
    fix_steps   = incident.get("fix", [])
    file_path   = _safe(incident.get("file_path", ""))
    media_title = _safe(incident.get("media_title", ""))
    first_seen  = _fmt_time(incident.get("first_seen", ""))
    last_seen   = _fmt_time(incident.get("last_seen", ""))
    count       = incident.get("occurrence_count", 1)
    related     = incident.get("related_ffmpeg", [])

    time_str = first_seen
    if last_seen and last_seen != first_seen:
        time_str = f"{first_seen} - {last_seen}"

    media_block = ""
    if file_path:
        media_block = (
            f'<div class="pe-media">'
            f'<span class="pe-media-label">File:</span>'
            f'<code>{file_path}</code>'
            f'</div>'
        )
    elif media_title:
        media_block = (
            f'<div class="pe-media">'
            f'<span class="pe-media-label">Media:</span>'
            f'<code>{media_title}</code>'
            f'</div>'
        )

    # Build fix list - detect ffmpeg command lines and wrap in code
    fix_items = ""
    for step in fix_steps:
        step_safe = _safe(step)
        if step_safe.startswith("ffmpeg "):
            fix_items += f'<li><code>{step_safe}</code></li>'
        else:
            fix_items += f'<li>{step_safe}</li>'

    fix_block = (
        f'<div class="pe-fix">'
        f'<div class="pe-fix-label">How to fix</div>'
        f'<ol>{fix_items}</ol>'
        f'</div>'
    ) if fix_items else ""

    # Related FFmpeg crash logs
    ffmpeg_block = ""
    if related:
        errs = ""
        for sess in related[:3]:
            dur = sess.get("duration_sec", 0)
            for e in sess.get("errors", [])[:2]:
                errs += f'<div class="pe-ffmpeg-err">{_safe(e)}</div>'
            errs += f'<div class="pe-ffmpeg-err" style="color:#555">Session duration: {dur}s</div>'
        ffmpeg_block = (
            f'<div class="pe-ffmpeg">'
            f'<div class="pe-ffmpeg-label">Related FFmpeg crash output</div>'
            f'{errs}'
            f'</div>'
        )

    return f"""
<div class="pe-card sev-{sev}">
  <div class="pe-header">
    <span class="pe-title">{title}</span>
    <div class="pe-badges">
      <span class="pe-badge" style="background:{sc}">{_sev_label(sev)}</span>
      <span class="pe-meta">x{count} occurrence{'s' if count != 1 else ''}</span>
      <span class="pe-meta">{time_str}</span>
    </div>
  </div>
  {media_block}
  <div class="pe-cause"><span class="pe-cause-label">Cause:</span>{cause}</div>
  {fix_block}
  {ffmpeg_block}
</div>
"""


# ---------------------------------------------------------------------------
# Section builder
# ---------------------------------------------------------------------------

def render_section(playback_data):
    if not playback_data:
        return ""

    incidents   = playback_data.get("incidents", [])
    total       = playback_data.get("total_incidents", 0)
    crash_loops = playback_data.get("crash_loop_sessions", 0)
    ff_count    = playback_data.get("ffmpeg_session_count", 0)
    error       = playback_data.get("error", "")

    if error and total == 0:
        return f"""
<div class="playback-errors-section">
  <h2>Playback Errors</h2>
  <div class="pe-card sev-INFO">
    <p style="color:var(--muted)">{_safe(error)}</p>
  </div>
</div>
"""

    if total == 0:
        return """
<div class="playback-errors-section">
  <h2>Playback Errors</h2>
  <p class="pe-empty">No playback errors detected in the last 24 hours.</p>
</div>
"""

    # Filter out pure INFO omdb noise if there are real errors too
    real_incidents = [i for i in incidents if i.get("pattern_id") != "omdb_error"]
    omdb_incidents = [i for i in incidents if i.get("pattern_id") == "omdb_error"]

    # Always show real incidents; collapse OMDB into a single note if present
    display_incidents = real_incidents
    omdb_note = ""
    if omdb_incidents:
        omdb_count = sum(i.get("occurrence_count", 1) for i in omdb_incidents)
        omdb_note = (
            f'<div class="pe-card sev-INFO" style="opacity:0.7">'
            f'<div class="pe-header">'
            f'<span class="pe-title">OMDB metadata fetch failures</span>'
            f'<span class="pe-meta">x{omdb_count} occurrences - not a playback issue</span>'
            f'</div>'
            f'<div class="pe-cause">The Open Movie Database API returned invalid JSON during '
            f'a metadata refresh scan. This runs in the background and does not affect playback. '
            f'Disable OMDB in Jellyfin Dashboard > Libraries if you want to suppress these.</div>'
            f'</div>'
        )

    cards_html = "".join(_render_card(i) for i in display_incidents)

    criticals = len([i for i in display_incidents if i.get("severity") == "CRITICAL"])
    warnings  = len([i for i in display_incidents if i.get("severity") == "WARNING"])

    badge_color = "#ef4444" if criticals > 0 else "#f59e0b" if warnings > 0 else "#3b82f6"
    badge_count = len(display_incidents)

    return f"""
<div class="playback-errors-section">
  <h2>Playback Errors
    <span class="section-badge" style="background:#2d1515;color:{badge_color}">{badge_count} incident{'s' if badge_count != 1 else ''}</span>
  </h2>
  <div class="pe-ffmpeg-stats">
    <div class="pe-stat-pill">FFmpeg sessions (24h): <strong>{ff_count}</strong></div>
    <div class="pe-stat-pill">Crash loops (&lt;15s): <strong style="color:{'#ef4444' if crash_loops > 0 else '#22c55e'}">{crash_loops}</strong></div>
  </div>
  {cards_html}
  {omdb_note}
</div>
"""


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render_playback_errors.py <data_file.json>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)

    playback_data = data.get("playback_errors", {})

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ background:#12121c; color:#ccc; font-family:sans-serif; padding:20px; }}
h2 {{ color:#e8e8f0; margin-bottom:12px; }}
.section-badge {{ font-size:0.6em; padding:2px 10px; border-radius:20px;
  vertical-align:middle; margin-left:10px; font-weight:700; }}
{get_css()}
</style></head><body>
{render_section(playback_data)}
</body></html>"""

    out = Path(sys.argv[1]).parent / "playback_errors_preview.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Preview written to: {out}")
