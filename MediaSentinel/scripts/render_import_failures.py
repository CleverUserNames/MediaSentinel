# -*- coding: utf-8 -*-
"""
render_import_failures.py
MediaSentinel - Import Failures HTML section renderer

Called from render_report.py to produce the "Import Failures" section.
Can also be run standalone against a data JSON for testing:
    python render_import_failures.py logs\data_20260904T120000.json
"""

import json
import sys
import os

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity_class(item):
    """CSS class based on how long the item has been stuck."""
    mins = item.get("age_minutes", 0)
    if mins > 60 * 24:      # > 1 day
        return "critical"
    if mins > 60 * 4:       # > 4 hours
        return "warning"
    return "info"


def _arr_badge(arr_name):
    color = "#5cad5c" if arr_name == "Sonarr" else "#c9a227"
    return f'<span class="arr-badge" style="background:{color}">{arr_name}</span>'


def _source_badge(source):
    label = "Queue" if source == "queue" else "History"
    color = "#3a7bd5" if source == "queue" else "#7b5ea7"
    return f'<span class="source-badge" style="background:{color}">{label}</span>'


def _retry_button(item, sonarr_url, radarr_url):
    """
    Build a Retry Import button. For queue items we link to the arr UI
    activity page. For history items we link to the history page.
    """
    arr     = item.get("arr", "")
    source  = item.get("source", "")
    base    = sonarr_url if arr == "Sonarr" else radarr_url

    if source == "queue":
        href = f"{base.rstrip('/')}/activity/queue"
        label = "Open Queue"
    else:
        href = f"{base.rstrip('/')}/activity/history"
        label = "Open History"

    return (
        f'<a href="{href}" target="_blank" class="action-btn retry-btn">'
        f'{label} -&gt;</a>'
    )


def _arr_ui_button(item, sonarr_url, radarr_url):
    arr  = item.get("arr", "")
    base = sonarr_url if arr == "Sonarr" else radarr_url
    href = f"{base.rstrip('/')}/activity/queue"
    return (
        f'<a href="{href}" target="_blank" class="action-btn open-btn">'
        f'Open in {arr} -&gt;</a>'
    )


# ---------------------------------------------------------------------------
# Diagnosis helpers
# ---------------------------------------------------------------------------
KNOWN_REASONS = [
    ("No files found are eligible for import",
     "The folder name doesn't match what {arr} expects. Check that the download "
     "landed in the correct completed-downloads path and that the folder is named "
     "in a way {arr} can parse (usually Title (Year) for movies or "
     "Series.Name.SXXEXX for episodes)."),

    ("Found matching",
     "A series/movie was matched via grab history but the folder path couldn't be "
     "resolved. Try manually importing from {arr}'s Activity > Queue using "
     "\"Manual Import\"."),

    ("Folder name mismatch",
     "The folder name in your downloads client doesn't match the expected naming "
     "pattern. Rename the folder or use Manual Import in {arr}."),

    ("Import failed",
     "Generic import failure. Check {arr} logs for detail. Common causes: "
     "permissions on the completed-downloads folder, folder naming, or the file "
     "being moved/deleted before import."),

    ("No space left",
     "The destination drive has no space available. Check K:\\ free space."),

    ("Unexpected file",
     "An extra file in the download folder confused {arr}'s import logic. "
     "Remove non-media files from the folder and retry."),
]


def _diagnose(messages, arr_name):
    """Return a plain-English diagnosis string for the given status messages."""
    combined = " ".join(messages).lower()
    for keyword, explanation in KNOWN_REASONS:
        if keyword.lower() in combined:
            return explanation.replace("{arr}", arr_name)
    return (
        f"Check {arr_name}'s Activity tab and logs for details. "
        "Manual Import is usually the fastest fix."
    )


# ---------------------------------------------------------------------------
# Card renderer
# ---------------------------------------------------------------------------

def _render_card(item, sonarr_url, radarr_url):
    sev   = _severity_class(item)
    arr   = item.get("arr", "")
    title = item.get("title", "Unknown")
    msgs  = item.get("messages", [])
    path  = item.get("output_path", "")
    age   = item.get("age_str", "?")
    client = item.get("download_client", "")
    diagnosis = _diagnose(msgs, arr)

    msg_html = "".join(
        f'<li class="failure-msg">{m}</li>' for m in msgs
    )
    path_html = (
        f'<div class="failure-path"><span class="path-label">File location:</span> '
        f'<code>{path}</code></div>'
    ) if path else ""
    client_html = (
        f'<span class="meta-pill">Client: {client}</span>'
    ) if client else ""

    retry_btn  = _retry_button(item, sonarr_url, radarr_url)
    arr_btn    = _arr_ui_button(item, sonarr_url, radarr_url)

    return f"""
<div class="import-failure-card sev-{sev}">
  <div class="failure-header">
    <span class="failure-title">{title}</span>
    <div class="failure-badges">
      {_arr_badge(arr)}
      {_source_badge(item.get('source', ''))}
      <span class="meta-pill age-pill">Age: {age}</span>
      {client_html}
    </div>
  </div>
  <ul class="failure-msgs">{msg_html}</ul>
  {path_html}
  <div class="failure-diagnosis">
    <span class="diag-label">&#128270; Diagnosis:</span> {diagnosis}
  </div>
  <div class="failure-actions">
    {retry_btn}
    {arr_btn}
  </div>
</div>
"""


# ---------------------------------------------------------------------------
# CSS (injected once into the report; render_report.py calls get_css())
# ---------------------------------------------------------------------------

SECTION_CSS = """
/* ---- Import Failures section ---- */
.import-failures-section { margin: 24px 0; }

.import-failure-card {
  background: #1e1e2e;
  border-radius: 10px;
  border-left: 5px solid #555;
  padding: 16px 20px;
  margin-bottom: 14px;
}
.import-failure-card.sev-critical { border-left-color: #e05252; }
.import-failure-card.sev-warning  { border-left-color: #e0a852; }
.import-failure-card.sev-info     { border-left-color: #5285e0; }

.failure-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.failure-title {
  font-size: 1.05em;
  font-weight: 600;
  color: #e8e8f0;
}
.failure-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.arr-badge, .source-badge, .meta-pill {
  font-size: 0.72em;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 20px;
  color: #fff;
  letter-spacing: 0.03em;
}
.meta-pill   { background: #2d2d42; color: #aaa; }
.age-pill    { background: #2d2d42; }

.failure-msgs {
  margin: 0 0 10px 0;
  padding-left: 18px;
}
.failure-msg {
  color: #d0cce0;
  font-size: 0.88em;
  margin-bottom: 3px;
  font-family: monospace;
}

.failure-path {
  background: #15151f;
  border-radius: 6px;
  padding: 7px 12px;
  margin-bottom: 10px;
  font-size: 0.83em;
  word-break: break-all;
}
.path-label { color: #888; margin-right: 6px; }
.failure-path code { color: #9ecbff; }

.failure-diagnosis {
  font-size: 0.87em;
  color: #b8b8cc;
  margin-bottom: 12px;
  line-height: 1.5;
}
.diag-label { color: #e0c87a; font-weight: 600; margin-right: 4px; }

.failure-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.action-btn {
  display: inline-block;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 0.82em;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  transition: opacity 0.15s;
}
.action-btn:hover { opacity: 0.82; }
.retry-btn { background: #3a5fa8; color: #fff; }
.open-btn  { background: #2d4a30; color: #8fd49a; }

.import-failures-empty {
  color: #6a6a80;
  font-style: italic;
  padding: 10px 0;
}
.import-failures-summary {
  font-size: 0.85em;
  color: #888;
  margin-bottom: 14px;
}
"""


def get_css():
    """Return the CSS string for inclusion in the report <style> block."""
    return SECTION_CSS


# ---------------------------------------------------------------------------
# Section HTML builder
# ---------------------------------------------------------------------------

def render_section(import_data, sonarr_url, radarr_url):
    """
    Build and return the full HTML for the Import Failures section.
    import_data is the dict from collect_import_failures.collect_import_failures().
    """
    if not import_data:
        return ""

    sonarr_items = import_data.get("sonarr", [])
    radarr_items = import_data.get("radarr", [])
    total        = import_data.get("total_failures", 0)
    collected_at = import_data.get("collected_at", "")

    if total == 0:
        return """
<div class="import-failures-section">
  <h2>&#128274; Import Failures</h2>
  <p class="import-failures-empty">&#10003; No stuck or failed imports detected.</p>
</div>
"""

    # Sort all items: queue before history, then by age descending (oldest first)
    all_items = sonarr_items + radarr_items
    all_items.sort(key=lambda x: (
        0 if x["source"] == "queue" else 1,
        -x.get("age_minutes", 0)
    ))

    cards_html = "".join(
        _render_card(item, sonarr_url, radarr_url) for item in all_items
    )

    sonarr_count = len(sonarr_items)
    radarr_count = len(radarr_items)
    summary_parts = []
    if sonarr_count:
        summary_parts.append(f"{sonarr_count} Sonarr")
    if radarr_count:
        summary_parts.append(f"{radarr_count} Radarr")
    summary_str = " &bull; ".join(summary_parts)

    return f"""
<div class="import-failures-section">
  <h2>&#128274; Import Failures
    <span class="section-badge critical-badge">{total} stuck</span>
  </h2>
  <p class="import-failures-summary">
    Downloads that completed in your download client but were not imported
    into your media library by Sonarr/Radarr. &nbsp;|&nbsp; {summary_str}
  </p>
  {cards_html}
</div>
"""


# ---------------------------------------------------------------------------
# Standalone test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render_import_failures.py <data_file.json>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)

    import_data = data.get("import_failures", {})
    cfg         = data.get("_config_snapshot", {})
    sonarr_url  = cfg.get("sonarr_url", "http://localhost:8989")
    radarr_url  = cfg.get("radarr_url", "http://localhost:7878")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ background:#12121c; color:#ccc; font-family:sans-serif; padding:20px; }}
h2 {{ color:#e8e8f0; }}
.section-badge {{ font-size:0.6em; padding:2px 10px; border-radius:20px; vertical-align:middle; margin-left:10px; }}
.critical-badge {{ background:#5c1a1a; color:#e05252; }}
{get_css()}
</style></head><body>
{render_section(import_data, sonarr_url, radarr_url)}
</body></html>"""

    out = os.path.join(os.path.dirname(sys.argv[1]), "import_failures_preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Preview written to: {out}")
