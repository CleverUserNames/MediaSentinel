#!/usr/bin/env python3
"""
render_report.py
Generates a self-contained HTML report from analysis + raw data JSONs.
"""

import json, sys, datetime, importlib.util, os
from pathlib import Path


def sanitize(text):
    """Strip any non-ASCII characters that may come from AI output."""
    if not isinstance(text, str):
        text = str(text)
    return text.encode('ascii', errors='ignore').decode('ascii')

def severity_color(s):
    return {"CRITICAL": "#ef4444", "WARNING": "#f59e0b", "INFO": "#3b82f6"}.get(s, "#6b7280")

def severity_icon(s):
    return {"CRITICAL": "[!]", "WARNING": "[W]", "INFO": "[i]"}.get(s, "[-]")

def health_color(score):
    if score >= 80: return "#22c55e"
    if score >= 60: return "#f59e0b"
    return "#ef4444"


# ---------------------------------------------------------------------------
# Import failures renderer (loaded from sibling script)
# ---------------------------------------------------------------------------

def _load_import_failures_renderer():
    """
    Dynamically load render_import_failures.py from the same directory.
    Returns (render_section_fn, get_css_fn) or (None, None) on failure.
    """
    try:
        script_path = Path(__file__).parent / "render_import_failures.py"
        if not script_path.exists():
            sys.stderr.write(f"[render_report] render_import_failures.py not found at {script_path}\n")
            return None, None
        spec = importlib.util.spec_from_file_location("render_import_failures", script_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.render_section, mod.get_css
    except Exception as e:
        sys.stderr.write(f"[render_report] failed to load render_import_failures: {e}\n")
        return None, None

_render_import_failures_section, _get_import_failures_css = _load_import_failures_renderer()


# ---------------------------------------------------------------------------
# Playback errors renderer (loaded from sibling script)
# ---------------------------------------------------------------------------

def _load_playback_errors_renderer():
    try:
        script_path = Path(__file__).parent / "render_playback_errors.py"
        if not script_path.exists():
            sys.stderr.write(f"[render_report] render_playback_errors.py not found at {script_path}\n")
            return None, None
        spec = importlib.util.spec_from_file_location("render_playback_errors", script_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.render_section, mod.get_css
    except Exception as e:
        sys.stderr.write(f"[render_report] failed to load render_playback_errors: {e}\n")
        return None, None

_render_playback_errors_section, _get_playback_errors_css = _load_playback_errors_renderer()


def render_playback_errors_html(raw):
    playback_data = raw.get("playback_errors", {})
    if _render_playback_errors_section is None:
        total = playback_data.get("total_incidents", 0)
        if total == 0:
            return '<p style="color:var(--muted);font-size:0.9rem">No playback errors detected.</p>'
        return (
            f'<div class="issue-card" style="border-left:4px solid #f59e0b">'
            f'<p style="color:#f59e0b">{total} playback incident(s) detected. '
            f'render_playback_errors.py not found.</p></div>'
        )
    return _render_playback_errors_section(playback_data)


def get_playback_errors_css():
    if _get_playback_errors_css is None:
        return ""
    return _get_playback_errors_css()


def render_import_failures_html(raw):
    """Build the import failures section HTML, or a safe fallback."""
    import_data = raw.get("import_failures", {})
    snapshot    = raw.get("_config_snapshot", {})
    sonarr_url  = snapshot.get("sonarr_url", "")
    radarr_url  = snapshot.get("radarr_url", "")

    if _render_import_failures_section is None:
        # Renderer not available  -  show raw count at minimum
        total = import_data.get("total_failures", 0)
        if total == 0:
            return '<p style="color:var(--muted);font-size:0.9rem">No stuck imports detected.</p>'
        return (
            f'<div class="issue-card" style="border-left:4px solid #f59e0b">'
            f'<p style="color:#f59e0b">{total} stuck import(s) detected. '
            f'render_import_failures.py not found  -  check your MediaSentinel scripts directory</p>'
            f'</div>'
        )

    # Pass error info through if collection itself failed
    if import_data.get("error") and not import_data.get("sonarr") and not import_data.get("radarr"):
        return (
            f'<div class="issue-card" style="border-left:4px solid #6b7280">'
            f'<p style="color:var(--muted)">Import failure check error: {sanitize(import_data["error"])}</p>'
            f'</div>'
        )

    return _render_import_failures_section(import_data, sonarr_url, radarr_url)


def get_import_failures_css():
    if _get_import_failures_css is None:
        return ""
    return _get_import_failures_css()


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(analysis, raw, cfg=None):
    if cfg is None:
        cfg = {}

    score     = analysis.get("health_score", 0)
    summary   = sanitize(analysis.get("summary", ""))
    issues    = analysis.get("issues", [])
    ta        = analysis.get("transcode_analysis", {})
    da        = analysis.get("disk_analysis", {})
    tips      = analysis.get("proactive_suggestions", [])
    ts        = raw.get("collected_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
    server_ip = raw.get("server_ip") or cfg.get("server", {}).get("ip", "")

    def issue_card(issue):
        sc = severity_color(issue["severity"])
        si = severity_icon(issue["severity"])
        steps = "".join(f"<li>{s}</li>" for s in issue.get("fix_steps", []))
        auto = issue.get("automation_script", "")
        auto_block = f'<pre class="auto-script">{auto}</pre>' if auto else ""
        return f"""
        <div class="issue-card" style="border-left: 4px solid {sc}">
          <div class="issue-header">
            <span class="badge" style="background:{sc}">{si} {issue['severity']}</span>
            <span class="service-tag">{issue.get('service','?')}</span>
            <span class="cat-tag">{issue.get('category','?')}</span>
            <span class="fix-type">[fix] {issue.get('fix_type','?')}</span>
          </div>
          <h3>{sanitize(issue['title'])}</h3>
          <p class="desc">{sanitize(issue.get('description',''))}</p>
          <div class="steps"><strong>Fix steps:</strong><ol>{steps}</ol></div>
          {auto_block}
        </div>"""

    issues_html = "\n".join(issue_card(i) for i in issues)

    transcode_rows = ""
    for s in ta.get("streams_detail", []):
        transcode_rows += f"<tr><td>{s.get('user')}</td><td>{s.get('title')}</td><td>{s.get('reason')}</td><td>{s.get('fix')}</td></tr>"

    tips_html = "".join(f"<li>{t}</li>" for t in [sanitize(x) for x in tips])

    # Build Jellyseer section from raw data
    seer_data       = next((s for s in raw.get("services", []) if s.get("service") == "jellyseer"), {})
    seer_connected  = seer_data.get("connected", False)
    seer_errors     = seer_data.get("errors", [])
    seer_total      = seer_data.get("total_requests", 0)
    seer_not_in_lib = seer_data.get("not_in_library", [])
    status_colors   = {
        "PENDING": "#f59e0b", "APPROVED": "#3b82f6", "PROCESSING": "#a78bfa",
        "FAILED": "#ef4444", "DECLINED": "#6b7280", "UNKNOWN": "#6b7280",
    }

    if not seer_connected:
        err_msg  = seer_errors[0].get("error", "Unknown error") if seer_errors else "Could not connect"
        seer_html = f'<div class="issue-card" style="border-left:4px solid #ef4444"><p style="color:#ef4444">Connection failed: {sanitize(err_msg)}</p></div>'
    elif not seer_not_in_lib:
        seer_html = '<div class="stat-card"><p style="color:var(--muted);font-size:0.9rem">All requested items are available in the library.</p></div>'
    else:
        rows = ""
        for item in seer_not_in_lib:
            sc    = status_colors.get(item.get("status", "UNKNOWN"), "#6b7280")
            title = sanitize(item.get("title", "Unknown"))
            year  = sanitize(item.get("year", ""))
            itype = sanitize(item.get("type", "")).upper()
            stat  = sanitize(item.get("status", "UNKNOWN"))
            reqby = sanitize(item.get("requested_by", ""))
            date  = sanitize(item.get("created", ""))
            title_year = title + (f" ({year})" if year else "")
            rows += (
                f'<tr><td style="font-weight:500">{title_year}</td>'
                f'<td><span style="color:var(--muted);font-size:0.8rem">{itype}</span></td>'
                f'<td><span style="background:{sc};color:#fff;padding:2px 8px;border-radius:99px;font-size:0.75rem;font-weight:700">{stat}</span></td>'
                f'<td style="color:var(--muted);font-size:0.85rem">{reqby}</td>'
                f'<td style="color:var(--muted);font-size:0.85rem">{date}</td></tr>'
            )
        seer_html = (
            f'<div style="margin-bottom:0.75rem;color:var(--muted);font-size:0.85rem">'
            f'{len(seer_not_in_lib)} of {seer_total} requested item(s) not yet in library</div>'
            f'<table class="transcode-table"><thead><tr>'
            f'<th>Title</th><th>Type</th><th>Status</th><th>Requested By</th><th>Date</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )

    # Build SMART drive health section
    smart_data   = raw.get("smart_health", {})
    smart_drives = smart_data.get("drives", [])
    sev_colors   = {"OK": "#22c55e", "INFO": "#38bdf8", "WARNING": "#f59e0b", "CRITICAL": "#ef4444"}

    if smart_data.get("error"):
        smart_html = '<div class="issue-card" style="border-left:4px solid #6b7280"><p style="color:var(--muted)">' + sanitize(smart_data["error"]) + '</p></div>'
    elif not smart_drives:
        smart_html = '<div class="stat-card"><p style="color:var(--muted);font-size:0.9rem">No drive data available.</p></div>'
    else:
        drive_cards = ""
        for d in smart_drives:
            sev     = d.get("severity", "OK")
            sc      = sev_colors.get(sev, "#6b7280")
            model   = sanitize(d.get("model", "Unknown"))
            serial  = sanitize(d.get("serial", "?"))
            cap     = sanitize(d.get("capacity", "?"))
            dtype   = sanitize(d.get("type", "?"))
            health  = sanitize(d.get("health", "?"))
            temp    = d.get("temperature_c")
            hours   = d.get("power_on_hours")
            realloc = d.get("reallocated_sectors", 0)
            pending = d.get("pending_sectors", 0)
            uncorr  = d.get("offline_uncorrectable", 0)
            data_r  = sanitize(d.get("data_read_tb") or "")
            data_w  = sanitize(d.get("data_written_tb") or "")
            issues_list = d.get("issues", [])
            temp_str  = f"{temp}C" if temp is not None else "N/A"
            hours_str = f"{hours:,}h" if hours else "N/A"
            if data_r or data_w:
                extra = f"R:{data_r} / W:{data_w}"
            else:
                extra = f"Realloc:{realloc} Pending:{pending} Uncorr:{uncorr}"
            iss_html = ""
            if issues_list:
                items = "".join(f"<li>{sanitize(i)}</li>" for i in issues_list)
                iss_html = f'<ul style="margin:6px 0 0;padding-left:1.2rem;font-size:0.8rem;color:#f59e0b">{items}</ul>'
            drive_cards += (
                f'<div style="background:var(--surface);border-radius:var(--radius);'
                f'padding:1rem;margin-bottom:0.75rem;border-left:4px solid {sc}">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">'
                f'<div><span style="font-weight:500;font-size:0.95rem">{model}</span>'
                f'<span style="color:var(--muted);font-size:0.8rem;margin-left:8px">{cap} {dtype}</span></div>'
                f'<span style="background:{sc};color:#fff;padding:2px 10px;border-radius:99px;font-size:0.75rem;font-weight:700">{sev}</span></div>'
                f'<table style="width:100%;margin-top:8px;font-size:0.85rem"><tr>'
                f'<td style="color:var(--muted)">S/N: {serial}</td>'
                f'<td style="color:var(--muted)">Health: <strong style="color:{sc}">{health}</strong></td>'
                f'<td style="color:var(--muted)">Temp: {temp_str}</td>'
                f'<td style="color:var(--muted)">Hours: {hours_str}</td>'
                f'<td style="color:var(--muted);font-size:0.8rem">{extra}</td>'
                f'</tr></table>{iss_html}</div>'
            )
        smart_html = drive_cards

    # Build active transcode section from raw data
    jf_data    = next((s for s in raw.get("services", []) if s.get("service") == "jellyfin"), {})
    transcodes = jf_data.get("active_transcodes", [])

    if not transcodes:
        transcode_html = '<div class="stat-card"><p style="color:var(--muted);font-size:0.9rem">No active transcodes.</p></div>'
    else:
        tc_cards = ""
        for t in transcodes:
            fv      = t.get("file_video", {})
            fa      = t.get("file_audio", {})
            tt      = t.get("transcoding_to", {})
            hw      = sanitize(t.get("hw_accel_type") or "software")
            title   = sanitize(t.get("now_playing") or "Unknown")
            user    = sanitize(t.get("user") or "?")
            client  = sanitize(t.get("client") or "?")
            device  = sanitize(t.get("device") or "?")
            reasons = t.get("reason_detail") or [sanitize(r) for r in t.get("transcode_reasons", [])]
            options = t.get("direct_play_options", [])

            hw_color = "#22c55e" if hw and hw != "software" else "#f59e0b"
            hw_label = hw.upper() if hw else "SOFTWARE"

            reason_rows = "".join(
                f'<tr><td style="color:#f59e0b;font-size:0.82rem;padding:4px 0">{sanitize(r)}</td></tr>'
                for r in reasons
            )
            option_rows = "".join(
                f'<tr><td style="color:#22c55e;font-size:0.82rem;padding:4px 0">{sanitize(o)}</td></tr>'
                for o in options
            )

            tc_cards += (
                f'<div style="background:var(--surface);border-radius:var(--radius);padding:1.25rem;'
                f'margin-bottom:1rem;border-left:4px solid #a78bfa">'

                # Header row: title + hw badge
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:0.75rem">'
                f'<div>'
                f'<div style="font-weight:600;font-size:1rem">{title}</div>'
                f'<div style="color:var(--muted);font-size:0.82rem;margin-top:2px">{user} on {client} ({device})</div>'
                f'</div>'
                f'<span style="background:{hw_color};color:#fff;padding:2px 10px;border-radius:99px;font-size:0.75rem;font-weight:700">{hw_label}</span>'
                f'</div>'

                # File spec table
                f'<table style="width:100%;border-collapse:collapse;margin-bottom:0.75rem">'
                f'<tr style="background:var(--surface2)">'
                f'<th style="padding:6px 10px;text-align:left;font-size:0.75rem;color:var(--muted);font-weight:500">Source File</th>'
                f'<th style="padding:6px 10px;text-align:left;font-size:0.75rem;color:var(--muted);font-weight:500">Streaming As</th>'
                f'</tr>'
                f'<tr>'
                f'<td style="padding:6px 10px;font-size:0.85rem;border-top:1px solid var(--border)">'
                f'<div>{sanitize(fv.get("codec","?")).upper()} {sanitize(fv.get("profile","?"))}</div>'
                f'<div style="color:var(--muted);font-size:0.8rem">{sanitize(fv.get("resolution","?"))} | {fv.get("bitrate_mbps","?")} Mbps | {sanitize(fv.get("hdr_type","?"))} | {fv.get("bit_depth","?")}bit</div>'
                f'<div style="color:var(--muted);font-size:0.8rem;margin-top:4px">{sanitize(fa.get("codec","?")).upper()} {sanitize(fa.get("title","?"))} {fa.get("channels","?")}ch</div>'
                f'</td>'
                f'<td style="padding:6px 10px;font-size:0.85rem;border-top:1px solid var(--border)">'
                f'<div>{sanitize(tt.get("video_codec","?")).upper()}</div>'
                f'<div style="color:var(--muted);font-size:0.8rem">{sanitize(tt.get("resolution","?"))} | {tt.get("bitrate_mbps","?")} Mbps</div>'
                f'<div style="color:var(--muted);font-size:0.8rem;margin-top:4px">{sanitize(tt.get("audio_codec","?")).upper()} {tt.get("audio_channels","?")}ch</div>'
                f'</td>'
                f'</tr>'
                f'</table>'

                # Reasons
                f'<div style="margin-bottom:0.5rem">'
                f'<div style="font-size:0.78rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px">Why Transcoding</div>'
                f'<table style="width:100%"><tbody>{reason_rows}</tbody></table>'
                f'</div>'

                + (
                    f'<div>'
                    f'<div style="font-size:0.78rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px">Options to Eliminate Transcode</div>'
                    f'<table style="width:100%"><tbody>{option_rows}</tbody></table>'
                    f'</div>'
                    if options else ""
                )

                + f'</div>'
            )
        transcode_html = tc_cards

    # Build recommendations section - card grid with posters and Jellyseer buttons
    rec_data     = raw.get("recommendations", {})
    rec_list     = rec_data.get("recommendations", [])
    top_genres   = rec_data.get("top_genres", [])
    rec_status   = rec_data.get("status", "")
    seer_base    = cfg.get("services", {}).get("jellyseer", {}).get("url", "")
    media_colors = {"movie": "#3b82f6", "tv": "#a78bfa"}

    if rec_status in ("missing", "error", "timeout") or not rec_list:
        rec_html = (
            '<div class="stat-card">'
            '<p style="color:var(--muted);font-size:0.9rem">No new recommendations found.</p>'
            '</div>'
        )
    else:
        genre_chips = "".join(
            '<span style="background:var(--surface2);color:var(--muted);padding:2px 8px;'
            'border-radius:99px;font-size:0.72rem;margin-right:4px">' + sanitize(g) + '</span>'
            for g in top_genres[:6]
        )
        cards = ""
        for item in rec_list:
            title     = sanitize(item.get("title", "Unknown"))
            year      = sanitize(item.get("year", ""))
            mtype     = sanitize(item.get("media_type", "movie"))
            rating    = item.get("rating", 0)
            reason    = sanitize(item.get("reason", "") or item.get("suggested_by", ""))
            genres    = ", ".join(sanitize(g) for g in item.get("genres", [])[:3])
            img_src   = sanitize(item.get("poster_url", ""))
            tmdb_id   = sanitize(item.get("tmdb_id", ""))
            mc        = media_colors.get(mtype, "#6b7280")
            title_yr  = title + (" (" + year + ")" if year else "")
            seer_link = seer_base + "/" + mtype + "/" + tmdb_id if seer_base and tmdb_id else ""

            poster_tag = (
                '<img src="' + img_src + '" alt="' + title + '"'
                ' style="width:100%;height:100%;object-fit:cover;display:block"'
                ' onerror="this.style.display=&quot;none&quot;;this.nextSibling.style.display=&quot;flex&quot;">'
                '<div style="display:none;width:100%;height:100%;align-items:center;'
                'justify-content:center;font-size:1.5rem;font-weight:700;'
                'color:var(--accent);background:var(--surface2)">' + title[:2].upper() + '</div>'
            ) if img_src else (
                '<div style="width:100%;height:100%;display:flex;align-items:center;'
                'justify-content:center;font-size:1.5rem;font-weight:700;'
                'color:var(--accent);background:var(--surface2)">' + title[:2].upper() + '</div>'
            )

            seer_btn = (
                '<a href="' + seer_link + '" target="_blank"'
                ' style="display:block;background:var(--accent);color:#0f172a;'
                'padding:5px 8px;border-radius:6px;font-size:0.72rem;font-weight:600;'
                'text-decoration:none;text-align:center;margin-top:6px">Request in Jellyseer</a>'
            ) if seer_link else ""

            cards += (
                '<div style="background:var(--surface);border-radius:var(--radius);'
                'overflow:hidden;display:flex;flex-direction:column">'
                '<div style="position:relative;aspect-ratio:2/3;overflow:hidden;flex-shrink:0">'
                + poster_tag +
                '<span style="position:absolute;top:5px;left:5px;background:' + mc + ';'
                'color:#fff;padding:1px 6px;border-radius:99px;font-size:0.65rem;font-weight:700">' + mtype.upper() + '</span>'
                '<span style="position:absolute;bottom:5px;right:5px;background:rgba(0,0,0,0.75);'
                'color:#fbbf24;padding:1px 6px;border-radius:99px;font-size:0.7rem;font-weight:600">' + str(rating) + '</span>'
                '</div>'
                '<div style="padding:0.6rem;display:flex;flex-direction:column;flex:1">'
                '<div style="font-size:0.82rem;font-weight:600;line-height:1.3;margin-bottom:3px">' + title_yr + '</div>'
                '<div style="font-size:0.68rem;color:var(--muted);margin-bottom:4px">' + genres + '</div>'
                '<div style="font-size:0.7rem;color:#7dd3fc;font-style:italic;line-height:1.4;flex:1">' + reason + '</div>'
                + seer_btn +
                '</div></div>'
            )

        rec_html = (
            '<div style="margin-bottom:0.75rem">' + genre_chips + '</div>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:0.75rem">'
            + cards + '</div>'
        )

    # Import failures
    import_failures_html = render_import_failures_html(raw)
    import_failures_css  = get_import_failures_css()
    playback_errors_html = render_playback_errors_html(raw)
    playback_errors_css  = get_playback_errors_css()

    # Count badges
    criticals = len([i for i in issues if i["severity"] == "CRITICAL"])
    warnings  = len([i for i in issues if i["severity"] == "WARNING"])
    infos     = len([i for i in issues if i["severity"] == "INFO"])
    import_total    = raw.get("import_failures", {}).get("total_failures", 0)
    playback_total  = raw.get("playback_errors", {}).get("total_incidents", 0)
    playback_crits  = len([i for i in raw.get("playback_errors", {}).get("incidents", []) if i.get("severity") == "CRITICAL"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <title>MediaSentinel Report - {ts}</title>
  <style>
    :root {{
      --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
      --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
      --border: #334155; --radius: 8px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.6; }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }}
    header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }}
    .logo {{ font-size: 1.4rem; font-weight: 700; letter-spacing: -0.5px; }}
    .logo span {{ color: var(--accent); }}
    .ts {{ color: var(--muted); font-size: 0.85rem; }}
    .score-ring {{ display: flex; align-items: center; gap: 1rem; }}
    .score-num {{ font-size: 3rem; font-weight: 800; color: {health_color(score)}; }}
    .score-label {{ color: var(--muted); font-size: 0.8rem; }}
    .summary-banner {{ background: var(--surface); border-radius: var(--radius); padding: 1rem 1.5rem; margin-bottom: 2rem; border-left: 4px solid {health_color(score)}; }}
    .stats-row {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 1rem; margin-bottom: 2rem; }}
    .stat-card {{ background: var(--surface); border-radius: var(--radius); padding: 1rem 1.25rem; text-align: center; }}
    .stat-num {{ font-size: 2rem; font-weight: 700; }}
    .stat-label {{ color: var(--muted); font-size: 0.8rem; }}
    .section-title {{ font-size: 1.1rem; font-weight: 600; margin: 2rem 0 1rem; color: var(--accent); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.85rem; }}
    .issue-card {{ background: var(--surface); border-radius: var(--radius); padding: 1.25rem; margin-bottom: 1rem; }}
    .issue-header {{ display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; }}
    .badge {{ padding: 0.2rem 0.6rem; border-radius: 99px; font-size: 0.75rem; font-weight: 700; color: #fff; }}
    .service-tag {{ background: var(--surface2); padding: 0.2rem 0.6rem; border-radius: 99px; font-size: 0.72rem; color: var(--muted); }}
    .cat-tag {{ background: var(--surface2); padding: 0.2rem 0.6rem; border-radius: 99px; font-size: 0.72rem; color: var(--muted); }}
    .fix-type {{ font-size: 0.72rem; color: var(--muted); }}
    h3 {{ font-size: 1rem; margin-bottom: 0.35rem; }}
    .desc {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 0.75rem; }}
    .steps {{ font-size: 0.875rem; }}
    .steps ol {{ padding-left: 1.25rem; }}
    .steps li {{ margin-bottom: 0.25rem; }}
    pre.auto-script {{ margin-top: 0.75rem; background: #0d1117; border-radius: 6px; padding: 0.75rem 1rem; font-size: 0.8rem; color: #7dd3fc; overflow-x: auto; }}
    .transcode-table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; background: var(--surface); border-radius: var(--radius); overflow: hidden; }}
    .transcode-table th {{ background: var(--surface2); padding: 0.65rem 1rem; text-align: left; font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
    .transcode-table td {{ padding: 0.65rem 1rem; border-top: 1px solid var(--border); }}
    .tips-list {{ background: var(--surface); border-radius: var(--radius); padding: 1rem 1.5rem; }}
    .tips-list li {{ margin-bottom: 0.5rem; font-size: 0.9rem; padding-left: 0.25rem; }}
    .section-badge {{ font-size: 0.6em; padding: 2px 10px; border-radius: 20px; vertical-align: middle; margin-left: 10px; font-weight: 700; }}
    .critical-badge {{ background: #5c1a1a; color: #e05252; }}
    footer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.8rem; text-align: center; }}
    {import_failures_css}
    {playback_errors_css}
  </style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <div class="logo">Media<span>Sentinel</span></div>
      <div class="ts">Report generated {ts} - {server_ip}</div>
    </div>
    <div class="score-ring">
      <div>
        <div class="score-num">{score}</div>
        <div class="score-label">Health Score</div>
      </div>
    </div>
  </header>

  <div class="summary-banner">{summary}</div>

  <div class="stats-row">
    <div class="stat-card">
      <div class="stat-num" style="color:#ef4444">{criticals}</div>
      <div class="stat-label">Critical Issues</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:#f59e0b">{warnings}</div>
      <div class="stat-label">Warnings</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:#3b82f6">{infos}</div>
      <div class="stat-label">Info</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:#a78bfa">{ta.get('active_count', 0)}</div>
      <div class="stat-label">Active Transcodes</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:{'#e05252' if import_total > 0 else '#22c55e'}">{import_total}</div>
      <div class="stat-label">Stuck Imports</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:{'#e05252' if playback_crits > 0 else '#f59e0b' if playback_total > 0 else '#22c55e'}">{playback_total}</div>
      <div class="stat-label">Playback Errors</div>
    </div>
  </div>

  <div class="section-title">Issues</div>
  {issues_html if issues_html else '<p style="color:var(--muted)">No issues detected.</p>'}

  <div class="section-title">Playback Errors</div>
  {playback_errors_html}

  <div class="section-title">Active Transcodes</div>
  {transcode_html}

  <div class="section-title">Import Failures</div>
  {import_failures_html}

  <div class="section-title">Jellyseer - Requested Items Not in Library</div>
  {seer_html}

  <div class="section-title">Recommended - Not In Library</div>
  {rec_html}

  <div class="section-title">Drive Health (SMART)</div>
  {smart_html}

  <div class="section-title">Proactive Suggestions</div>
  <ul class="tips-list">{tips_html if tips_html else '<li>No additional suggestions.</li>'}</ul>

  <footer>MediaSentinel - Stack: Jellyfin - Sonarr - Radarr - Jellyseer - AI: LM Studio</footer>
</div>
</body>
</html>"""


def main():
    analysis_file = sys.argv[1] if len(sys.argv) > 1 else None
    data_file     = sys.argv[2] if len(sys.argv) > 2 else None

    analysis = json.load(open(analysis_file, encoding='utf-8')) if analysis_file else {}
    raw      = json.load(open(data_file, encoding='utf-8'))     if data_file     else {}

    _cfg_path = Path(__file__).parent.parent / "config" / "config.json"
    cfg = json.load(open(_cfg_path, encoding="utf-8")) if _cfg_path.exists() else {}
    sys.stdout.reconfigure(encoding='utf-8')
    print(render(analysis, raw, cfg))

if __name__ == "__main__":
    main()
