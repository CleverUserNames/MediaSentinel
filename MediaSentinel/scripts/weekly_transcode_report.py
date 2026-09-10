#!/usr/bin/env python3
"""
weekly_transcode_report.py
Reads 7 days of transcode history from SQLite,
analyzes patterns with local AI, and generates
an HTML report with Tdarr/fix recommendations.

Usage: python weekly_transcode_report.py [--days N]
"""

import json
import sys
import sqlite3
import datetime
import requests
import re
from pathlib import Path
from collections import defaultdict

DB_PATH     = Path(__file__).parent.parent / "logs" / "transcodes.db"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"
LOG_DIR     = Path(__file__).parent.parent / "logs"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_db():
    if not DB_PATH.exists():
        print("No transcode database found. Run the monitor first to collect data.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_week_data(conn, days=7):
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT * FROM transcode_sessions
        WHERE date >= ?
        ORDER BY date DESC, title
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def build_analysis_summary(rows):
    """Build structured summary of transcode patterns for AI analysis."""
    if not rows:
        return None, {}

    # Frequency counts
    by_title       = defaultdict(int)
    by_reason      = defaultdict(int)
    by_client      = defaultdict(int)
    by_src_audio   = defaultdict(int)
    by_src_video   = defaultdict(int)
    by_hdr         = defaultdict(int)
    by_dst_video   = defaultdict(int)
    hw_accel_count = 0
    sw_only_count  = 0

    title_reasons  = defaultdict(set)   # title -> set of reasons
    title_clients  = defaultdict(set)   # title -> set of clients
    title_audio    = defaultdict(set)   # title -> audio codecs
    title_video    = defaultdict(set)   # title -> video codecs

    for r in rows:
        title   = r.get("title", "Unknown")
        client  = r.get("client", "Unknown")
        reasons = []
        try:
            reasons = json.loads(r.get("transcode_reasons") or "[]")
        except Exception:
            pass

        by_title[title]  += 1
        by_client[client] += 1

        src_audio = r.get("src_audio_codec") or "unknown"
        src_video = r.get("src_video_codec") or "unknown"
        hdr       = r.get("src_hdr_type") or "SDR"
        dst_video = r.get("dst_video_codec") or "unknown"

        by_src_audio[f"{src_audio} ({r.get('src_audio_channels','?')}ch)"] += 1
        by_src_video[f"{src_video} {r.get('src_video_profile','')}".strip()] += 1
        by_hdr[hdr] += 1
        by_dst_video[dst_video] += 1

        for reason in reasons:
            by_reason[reason] += 1
            title_reasons[title].add(reason)

        title_clients[title].add(client)
        title_audio[title].add(src_audio)
        title_video[title].add(src_video)

        if r.get("hw_accel_active"):
            hw_accel_count += 1
        else:
            sw_only_count += 1

    # Build text summary
    lines = [
        f"TRANSCODE HISTORY ANALYSIS - Last {len(set(r['date'] for r in rows))} days",
        f"Total transcode events: {len(rows)}",
        f"Unique titles: {len(by_title)}",
        f"Hardware accelerated: {hw_accel_count} | Software only: {sw_only_count}",
        "",
        "TOP TRANSCODED TITLES (most frequent):",
    ]
    for title, count in sorted(by_title.items(), key=lambda x: -x[1])[:15]:
        reasons_str = ", ".join(sorted(title_reasons[title]))
        clients_str = ", ".join(sorted(title_clients[title]))
        audio_str   = ", ".join(sorted(title_audio[title]))
        video_str   = ", ".join(sorted(title_video[title]))
        lines.append(f"  [{count}x] {title}")
        lines.append(f"       Clients: {clients_str}")
        lines.append(f"       Source: {video_str} | Audio: {audio_str}")
        lines.append(f"       Reasons: {reasons_str}")

    lines += ["", "TRANSCODE REASONS (by frequency):"]
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count}x] {reason}")

    lines += ["", "SOURCE AUDIO CODECS CAUSING TRANSCODES:"]
    for codec, count in sorted(by_src_audio.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count}x] {codec}")

    lines += ["", "SOURCE VIDEO CODECS:"]
    for codec, count in sorted(by_src_video.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count}x] {codec}")

    lines += ["", "HDR TYPES:"]
    for hdr, count in sorted(by_hdr.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count}x] {hdr}")

    lines += ["", "CLIENTS TRIGGERING TRANSCODES:"]
    for client, count in sorted(by_client.items(), key=lambda x: -x[1]):
        lines.append(f"  [{count}x] {client}")

    stats = {
        "total":       len(rows),
        "by_title":    dict(by_title),
        "by_reason":   dict(by_reason),
        "by_client":   dict(by_client),
        "by_audio":    dict(by_src_audio),
        "by_video":    dict(by_src_video),
        "by_hdr":      dict(by_hdr),
        "hw_pct":      round(100 * hw_accel_count / max(len(rows), 1), 1),
    }

    return "\n".join(lines), stats


SYSTEM_PROMPT = """You are a media server transcoding specialist analyzing weekly transcode history.
Your goal is to identify systematic problems and provide SPECIFIC, ACTIONABLE fixes using Tdarr or FFmpeg.

The user has access to:
- Tdarr (automated media processing/transcoding pipeline)
- FFmpeg
- Jellyfin (media server)

For each issue identified, provide:
1. Root cause
2. Which files/codecs are affected
3. Specific Tdarr community plugin name OR FFmpeg command to fix it
4. Jellyfin setting to change if applicable
5. Priority (how much transcoding it would eliminate)

Output ONLY valid JSON:
{
  "summary": "one paragraph overview of the week",
  "total_events": 0,
  "hw_acceleration_pct": 0,
  "top_issues": [
    {
      "id": "unique-slug",
      "priority": "HIGH|MEDIUM|LOW",
      "title": "short issue name",
      "affected_count": 0,
      "affected_titles": ["title1", "title2"],
      "root_cause": "technical explanation",
      "fix_type": "tdarr|jellyfin_setting|client_setting|ffmpeg",
      "fix_detail": {
        "tdarr_plugin": "plugin name if applicable",
        "tdarr_flow": "recommended Tdarr flow steps",
        "ffmpeg_cmd": "ffmpeg command if applicable",
        "jellyfin_setting": "setting path and value",
        "description": "step by step fix instructions"
      }
    }
  ],
  "tdarr_recommendations": [
    {
      "priority": "HIGH|MEDIUM|LOW",
      "plugin_or_flow": "name",
      "purpose": "what it fixes",
      "affected_files_estimate": "how many files need this"
    }
  ],
  "proactive_suggestions": ["suggestion1", "suggestion2"]
}"""


def analyze_with_ai(cfg, summary_text, stats):
    host  = cfg["server"]["local_ai_host"]
    model = cfg["server"]["local_ai_model"]

    user_prompt = (
        f"Analyze this weekly transcode report and provide recommendations:\n\n"
        f"{summary_text}\n\n"
        f"Return JSON as specified."
    )

    try:
        r = requests.post(
            f"{host}/v1/chat/completions",
            json={
                "model":       model,
                "messages":    [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens":  3000,
                "stream":      False,
            },
            headers={"Content-Type": "application/json"},
            timeout=180
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$",          "", raw, flags=re.MULTILINE)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(match.group(0) if match else raw)
    except Exception as e:
        return {"error": str(e), "summary": "AI analysis failed", "top_issues": []}


def sanitize(text):
    if not isinstance(text, str):
        text = str(text)
    return text.encode("ascii", errors="ignore").decode("ascii")


def render_html(analysis, stats, rows, week_start, week_end):
    """Generate the weekly transcode report HTML."""

    pri_colors = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#3b82f6"}
    fix_colors = {"tdarr": "#a78bfa", "jellyfin_setting": "#38bdf8",
                  "client_setting": "#22c55e", "ffmpeg": "#f59e0b"}

    # Top titles table
    by_title = stats.get("by_title", {})
    title_rows = ""
    for title, count in sorted(by_title.items(), key=lambda x: -x[1])[:20]:
        title_rows += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-top:1px solid var(--border);font-weight:500'>{sanitize(title)}</td>"
            f"<td style='padding:8px 12px;border-top:1px solid var(--border);text-align:center'>"
            f"<span style='background:#334155;padding:2px 10px;border-radius:99px;font-size:0.85rem'>{count}</span></td>"
            f"</tr>"
        )

    # Reason breakdown
    reason_bars = ""
    by_reason = stats.get("by_reason", {})
    max_count  = max(by_reason.values()) if by_reason else 1
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        pct = int(100 * count / max_count)
        reason_bars += (
            f"<div style='margin-bottom:10px'>"
            f"<div style='display:flex;justify-content:space-between;margin-bottom:3px'>"
            f"<span style='font-size:0.85rem'>{sanitize(reason)}</span>"
            f"<span style='color:var(--muted);font-size:0.8rem'>{count}x</span></div>"
            f"<div style='background:var(--surface2);border-radius:4px;height:8px'>"
            f"<div style='background:#38bdf8;border-radius:4px;height:8px;width:{pct}%'></div>"
            f"</div></div>"
        )

    # Audio codec breakdown
    audio_rows = ""
    for codec, count in sorted(stats.get("by_audio", {}).items(), key=lambda x: -x[1]):
        audio_rows += (
            f"<div style='display:flex;justify-content:space-between;padding:6px 0;"
            f"border-top:1px solid var(--border);font-size:0.85rem'>"
            f"<span>{sanitize(codec)}</span>"
            f"<span style='color:#f59e0b;font-weight:600'>{count}x</span></div>"
        )

    # Issue cards
    issue_cards = ""
    for issue in analysis.get("top_issues", []):
        pri   = sanitize(issue.get("priority", "LOW"))
        pc    = pri_colors.get(pri, "#6b7280")
        ft    = sanitize(issue.get("fix_type", ""))
        fc    = fix_colors.get(issue.get("fix_type", ""), "#6b7280")
        title = sanitize(issue.get("title", ""))
        cause = sanitize(issue.get("root_cause", ""))
        count = issue.get("affected_count", 0)
        titles = [sanitize(t) for t in issue.get("affected_titles", [])[:5]]

        fd = issue.get("fix_detail", {}) or {}
        fix_parts = []
        if fd.get("tdarr_plugin"):
            fix_parts.append(f"<div style='margin-bottom:6px'><strong>Tdarr Plugin:</strong> {sanitize(fd['tdarr_plugin'])}</div>")
        if fd.get("tdarr_flow"):
            fix_parts.append(f"<div style='margin-bottom:6px'><strong>Tdarr Flow:</strong> {sanitize(fd['tdarr_flow'])}</div>")
        if fd.get("jellyfin_setting"):
            fix_parts.append(f"<div style='margin-bottom:6px'><strong>Jellyfin:</strong> {sanitize(fd['jellyfin_setting'])}</div>")
        if fd.get("ffmpeg_cmd"):
            fix_parts.append(f"<pre style='background:#0d1117;border-radius:6px;padding:8px 12px;font-size:0.78rem;color:#7dd3fc;overflow-x:auto;margin:6px 0'>{sanitize(fd['ffmpeg_cmd'])}</pre>")
        if fd.get("description"):
            fix_parts.append(f"<div style='color:var(--muted);font-size:0.85rem;margin-top:6px'>{sanitize(fd['description'])}</div>")

        titles_str = ""
        if titles:
            titles_str = "<div style='margin-top:6px;font-size:0.8rem;color:var(--muted)'>" + " | ".join(titles) + "</div>"

        issue_cards += (
            f"<div style='background:var(--surface);border-radius:var(--radius);padding:1.25rem;"
            f"margin-bottom:1rem;border-left:4px solid {pc}'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;flex-wrap:wrap;gap:6px'>"
            f"<div style='display:flex;gap:8px;align-items:center'>"
            f"<span style='background:{pc};color:#fff;padding:2px 8px;border-radius:99px;font-size:0.72rem;font-weight:700'>{pri}</span>"
            f"<span style='background:{fc};color:#fff;padding:2px 8px;border-radius:99px;font-size:0.72rem;font-weight:600'>{ft}</span>"
            f"<span style='background:var(--surface2);color:var(--muted);padding:2px 8px;border-radius:99px;font-size:0.72rem'>{count} events</span>"
            f"</div></div>"
            f"<h3 style='font-size:1rem;margin-bottom:4px'>{title}</h3>"
            f"<p style='color:var(--muted);font-size:0.88rem;margin-bottom:0.75rem'>{cause}</p>"
            f"{titles_str}"
            f"<div style='margin-top:0.75rem;padding-top:0.75rem;border-top:1px solid var(--border)'>"
            f"{''.join(fix_parts)}"
            f"</div></div>"
        )

    # Tdarr recommendations table
    tdarr_rows = ""
    for rec in analysis.get("tdarr_recommendations", []):
        pri = sanitize(rec.get("priority", "LOW"))
        pc  = pri_colors.get(pri, "#6b7280")
        tdarr_rows += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-top:1px solid var(--border)'>"
            f"<span style='background:{pc};color:#fff;padding:1px 6px;border-radius:4px;font-size:0.72rem'>{pri}</span></td>"
            f"<td style='padding:8px 12px;border-top:1px solid var(--border);font-weight:500'>{sanitize(rec.get('plugin_or_flow',''))}</td>"
            f"<td style='padding:8px 12px;border-top:1px solid var(--border);color:var(--muted);font-size:0.85rem'>{sanitize(rec.get('purpose',''))}</td>"
            f"<td style='padding:8px 12px;border-top:1px solid var(--border);color:var(--muted);font-size:0.82rem'>{sanitize(rec.get('affected_files_estimate',''))}</td>"
            f"</tr>"
        )

    # Suggestions
    suggestions_html = "".join(
        f"<li style='margin-bottom:6px'>{sanitize(s)}</li>"
        for s in analysis.get("proactive_suggestions", [])
    )

    summary = sanitize(analysis.get("summary", "No summary available."))
    total   = stats.get("total", 0)
    hw_pct  = stats.get("hw_pct", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MediaSentinel Weekly Transcode Report</title>
  <style>
    :root {{
      --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
      --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
      --border: #334155; --radius: 8px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text);
            font-family: 'Segoe UI', system-ui, sans-serif; line-height: 1.6; }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }}
    header {{ display: flex; justify-content: space-between; align-items: center;
              margin-bottom: 2rem; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); }}
    .logo {{ font-size: 1.4rem; font-weight: 700; }}
    .logo span {{ color: var(--accent); }}
    .period {{ color: var(--muted); font-size: 0.85rem; }}
    .stats-row {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 1rem; margin-bottom: 2rem; }}
    .stat {{ background: var(--surface); border-radius: var(--radius); padding: 1rem 1.25rem; text-align: center; }}
    .stat-num {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
    .stat-label {{ color: var(--muted); font-size: 0.8rem; }}
    .section-title {{ font-size: 0.82rem; font-weight: 600; color: var(--accent);
                      text-transform: uppercase; letter-spacing: 0.08em; margin: 2rem 0 1rem; }}
    .summary {{ background: var(--surface); border-radius: var(--radius);
                padding: 1rem 1.5rem; margin-bottom: 2rem;
                border-left: 4px solid var(--accent); font-size: 0.95rem; color: var(--muted); }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-bottom: 2rem; }}
    .panel {{ background: var(--surface); border-radius: var(--radius); padding: 1.25rem; }}
    .panel h4 {{ font-size: 0.9rem; color: var(--accent); margin-bottom: 1rem; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--surface);
             border-radius: var(--radius); overflow: hidden; }}
    th {{ background: var(--surface2); padding: 8px 12px; text-align: left;
          font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }}
    footer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border);
              color: var(--muted); font-size: 0.8rem; text-align: center; }}
    ul {{ padding-left: 1.25rem; }}
    li {{ margin-bottom: 4px; font-size: 0.9rem; }}
  </style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <div class="logo">Media<span>Sentinel</span> Weekly Transcode Report</div>
      <div class="period">Week of {week_start} to {week_end}</div>
    </div>
  </header>

  <div class="stats-row">
    <div class="stat"><div class="stat-num">{total}</div><div class="stat-label">Transcode Events</div></div>
    <div class="stat"><div class="stat-num">{len(by_title)}</div><div class="stat-label">Unique Titles</div></div>
    <div class="stat"><div class="stat-num">{hw_pct}%</div><div class="stat-label">HW Accelerated</div></div>
    <div class="stat"><div class="stat-num">{len(analysis.get('top_issues',[]))}</div><div class="stat-label">Issues Found</div></div>
  </div>

  <div class="summary">{summary}</div>

  <div class="two-col">
    <div class="panel">
      <h4>Top Transcoded Titles</h4>
      <table><thead><tr><th>Title</th><th>Events</th></tr></thead>
      <tbody>{title_rows}</tbody></table>
    </div>
    <div>
      <div class="panel" style="margin-bottom:1rem">
        <h4>Transcode Reasons</h4>
        {reason_bars}
      </div>
      <div class="panel">
        <h4>Source Audio Codecs</h4>
        {audio_rows}
      </div>
    </div>
  </div>

  <div class="section-title">Issues and Fixes</div>
  {issue_cards if issue_cards else '<div class="panel" style="color:var(--muted)">No issues identified.</div>'}

  <div class="section-title">Tdarr Recommendations</div>
  <table>
    <thead><tr><th>Priority</th><th>Plugin / Flow</th><th>Purpose</th><th>Est. Files</th></tr></thead>
    <tbody>{tdarr_rows if tdarr_rows else '<tr><td colspan="4" style="padding:12px;color:var(--muted)">No Tdarr recommendations.</td></tr>'}</tbody>
  </table>

  <div class="section-title">Proactive Suggestions</div>
  <div class="panel"><ul>{suggestions_html}</ul></div>

  <footer>MediaSentinel Weekly Transcode Analysis - Powered by LM Studio</footer>
</div>
</body>
</html>"""


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Days of history to analyze")
    args = parser.parse_args()

    cfg  = load_config()
    conn = get_db()

    now        = datetime.datetime.utcnow()
    week_end   = now.strftime("%Y-%m-%d")
    week_start = (now - datetime.timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"Fetching {args.days} days of transcode history...", file=sys.stderr)
    rows = fetch_week_data(conn, args.days)
    conn.close()

    if not rows:
        print("No transcode data found for this period.", file=sys.stderr)
        sys.exit(0)

    print(f"Found {len(rows)} events. Building summary...", file=sys.stderr)
    summary_text, stats = build_analysis_summary(rows)

    print("Sending to AI for analysis...", file=sys.stderr)
    analysis = analyze_with_ai(cfg, summary_text, stats)

    # Save report
    out_path = LOG_DIR / f"weekly_transcode_{week_start}.html"
    html     = render_html(analysis, stats, rows, week_start, week_end)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Always overwrite the latest symlink
    latest = LOG_DIR / "weekly_transcode_latest.html"
    with open(latest, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report saved: {out_path}", file=sys.stderr)
    print(str(out_path))


if __name__ == "__main__":
    main()
