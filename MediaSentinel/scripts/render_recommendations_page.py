#!/usr/bin/env python3
"""
render_recommendations_page.py
Generates a standalone Jellyseer-style recommendations page
served from the media server. Each card deep-links to Jellyseer
for one-tap requesting on Android.
"""

import json, sys, datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"

def sanitize(text):
    if not isinstance(text, str):
        text = str(text)
    return text.encode('ascii', errors='ignore').decode('ascii')

def main():
    data_file = sys.argv[1] if len(sys.argv) > 1 else None
    if not data_file:
        print("Usage: render_recommendations_page.py <data.json>")
        sys.exit(1)

    cfg      = json.load(open(CONFIG_PATH, encoding='utf-8'))
    raw      = json.load(open(data_file, encoding='utf-8'))
    seer_url = cfg['services']['jellyseer']['url']

    rec_data   = raw.get('recommendations', {})
    rec_list   = rec_data.get('recommendations', [])
    top_genres = rec_data.get('top_genres', [])
    watch_sum  = rec_data.get('watch_summary', {})
    ts         = raw.get('collected_at', datetime.datetime.now(datetime.timezone.utc).isoformat())[:19].replace('T', ' ') + ' UTC'
    status     = rec_data.get('status', '')

    # Build cards HTML
    if status != 'ok' or not rec_list:
        cards_html = '<div class="empty">No recommendations available yet. Run the monitor to generate suggestions.</div>'
    else:
        cards = []
        for item in rec_list:
            tmdb_id    = sanitize(item.get('tmdb_id', ''))
            title      = sanitize(item.get('title', 'Unknown'))
            year       = sanitize(item.get('year', ''))
            media_type = sanitize(item.get('media_type', 'movie'))
            rating     = item.get('rating', 0)
            reason     = sanitize(item.get('reason', ''))
            suggested  = sanitize(item.get('suggested_by', ''))
            genres     = ', '.join(sanitize(g) for g in item.get('genres', [])[:3])
            overview   = sanitize(item.get('overview', ''))[:180]
            tmdb_url   = sanitize(item.get('tmdb_url', ''))
            img_src    = sanitize(item.get('poster_url', ''))

            # Jellyseer deep link - opens directly to the title page
            seer_link  = f"{seer_url}/{media_type}/{tmdb_id}"

            type_color = '#3b82f6' if media_type == 'movie' else '#a78bfa'
            type_label = 'MOVIE' if media_type == 'movie' else 'TV'

            stars = ''
            filled = int(round(rating / 2))
            for s in range(5):
                stars += '*' if s < filled else '-'

            cards.append(f'''
            <div class="card">
              <div class="poster-wrap">
                <img
                  src="{img_src}"
                  onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
                  alt="{title}"
                  class="poster"
                  style="{'display:none' if not img_src else ''}"
                />
                <div class="poster-fallback" style="display:{'none' if img_src else 'flex'}">
                  <span>{title[:2].upper()}</span>
                </div>
                <span class="type-badge" style="background:{type_color}">{type_label}</span>
                <div class="rating-badge">{rating}/10</div>
              </div>
              <div class="card-body">
                <div class="card-title">{title}{f" ({year})" if year else ""}</div>
                <div class="genres">{genres}</div>
                <div class="reason">{reason if reason else suggested}</div>
                {f'<div class="overview">{overview}{"..." if len(overview) == 180 else ""}</div>' if overview else ''}
                <div class="card-actions">
                  <a href="{seer_link}" target="_blank" class="btn-request">
                    Request in Jellyseer
                  </a>
                  {f'<a href="{tmdb_url}" target="_blank" class="btn-tmdb">TMDB</a>' if tmdb_url else ''}
                </div>
              </div>
            </div>''')
        cards_html = '\n'.join(cards)

    # Watch summary chips
    user_chips = ''
    for uname, titles in watch_sum.items():
        recent = ', '.join(sanitize(t) for t in titles[:6])
        user_chips += f'<div class="user-chip"><span class="uname">{sanitize(uname)}</span> recently watched: {recent}...</div>'

    genre_chips = ''.join(
        f'<span class="genre-chip">{sanitize(g)}</span>' for g in top_genres[:8]
    )

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MediaSentinel Recommendations</title>
  <style>
    :root {{
      --bg: #0f172a; --surface: #1e293b; --surface2: #334155;
      --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
      --border: #334155; --radius: 12px; --card-w: 220px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg); color: var(--text);
      font-family: 'Segoe UI', system-ui, sans-serif;
      line-height: 1.5; padding: 0 0 3rem;
    }}
    header {{
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 1rem 1.5rem; display: flex; justify-content: space-between;
      align-items: center; flex-wrap: wrap; gap: 0.5rem;
      position: sticky; top: 0; z-index: 10;
    }}
    .logo {{ font-size: 1.2rem; font-weight: 700; }}
    .logo span {{ color: var(--accent); }}
    .ts {{ color: var(--muted); font-size: 0.8rem; }}
    .seer-link {{
      background: var(--accent); color: #0f172a; padding: 6px 14px;
      border-radius: 99px; font-size: 0.82rem; font-weight: 600;
      text-decoration: none;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; padding: 1.5rem; }}
    .section-label {{
      font-size: 0.75rem; font-weight: 600; color: var(--accent);
      text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.75rem;
    }}
    .user-chips {{ margin-bottom: 0.5rem; }}
    .user-chip {{
      font-size: 0.82rem; color: var(--muted); margin-bottom: 4px;
      padding: 6px 10px; background: var(--surface); border-radius: 8px;
    }}
    .uname {{ color: var(--accent); font-weight: 500; }}
    .genre-chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0.75rem 0 1.5rem; }}
    .genre-chip {{
      background: var(--surface2); color: var(--muted);
      padding: 3px 10px; border-radius: 99px; font-size: 0.75rem;
    }}
    h2 {{
      font-size: 1.3rem; font-weight: 700; margin: 1.5rem 0 1rem;
      display: flex; align-items: center; gap: 10px;
    }}
    .count {{
      background: var(--surface2); color: var(--muted);
      padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; font-weight: 400;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(var(--card-w), 1fr));
      gap: 1.25rem;
    }}
    .card {{
      background: var(--surface); border-radius: var(--radius);
      overflow: hidden; display: flex; flex-direction: column;
      transition: transform 0.15s;
    }}
    .card:hover {{ transform: translateY(-3px); }}
    .poster-wrap {{
      position: relative; aspect-ratio: 2/3; overflow: hidden;
      background: var(--surface2); flex-shrink: 0;
    }}
    .poster {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .poster-fallback {{
      width: 100%; height: 100%; align-items: center; justify-content: center;
      font-size: 3rem; font-weight: 700; color: var(--accent); background: var(--surface2);
    }}
    .type-badge {{
      position: absolute; top: 8px; left: 8px;
      color: #fff; padding: 2px 8px; border-radius: 99px;
      font-size: 0.68rem; font-weight: 700;
    }}
    .rating-badge {{
      position: absolute; bottom: 8px; right: 8px;
      background: rgba(0,0,0,0.75); color: #fbbf24;
      padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; font-weight: 600;
    }}
    .card-body {{ padding: 0.875rem; display: flex; flex-direction: column; flex: 1; }}
    .card-title {{ font-size: 0.95rem; font-weight: 600; margin-bottom: 4px; line-height: 1.3; }}
    .genres {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 6px; }}
    .reason {{
      font-size: 0.78rem; color: #7dd3fc; margin-bottom: 6px;
      font-style: italic; line-height: 1.4;
    }}
    .overview {{ font-size: 0.78rem; color: var(--muted); line-height: 1.5; flex: 1; margin-bottom: 10px; }}
    .card-actions {{ display: flex; gap: 6px; margin-top: auto; padding-top: 8px; }}
    .btn-request {{
      flex: 1; background: var(--accent); color: #0f172a;
      padding: 7px 10px; border-radius: 8px; font-size: 0.78rem;
      font-weight: 600; text-decoration: none; text-align: center;
      display: block;
    }}
    .btn-request:hover {{ background: #7dd3fc; }}
    .btn-tmdb {{
      background: var(--surface2); color: var(--muted);
      padding: 7px 10px; border-radius: 8px; font-size: 0.78rem;
      text-decoration: none; text-align: center; white-space: nowrap;
    }}
    .empty {{
      text-align: center; color: var(--muted); padding: 4rem 2rem;
      font-size: 1rem;
    }}
    footer {{
      text-align: center; color: var(--muted); font-size: 0.78rem;
      margin-top: 3rem; padding-top: 1.5rem;
      border-top: 1px solid var(--border);
    }}
    @media (max-width: 480px) {{
      :root {{ --card-w: 160px; }}
      .card-title {{ font-size: 0.85rem; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <div class="logo">Media<span>Sentinel</span> Picks</div>
      <div class="ts">Updated {ts}</div>
    </div>
    <a href="{seer_url}" target="_blank" class="seer-link">Open Jellyseer</a>
  </header>

  <div class="container">
    <div class="section-label" style="margin-top:1rem">Based on watch activity</div>
    <div class="user-chips">{user_chips}</div>
    <div class="section-label">Top genres</div>
    <div class="genre-chips">{genre_chips}</div>

    <h2>Recommended for You <span class="count">{len(rec_list)} titles</span></h2>
    <div class="cards">
      {cards_html}
    </div>

    <footer>MediaSentinel &middot; Powered by TMDB &middot; Request via Jellyseer</footer>
  </div>
</body>
</html>'''

    sys.stdout.reconfigure(encoding='utf-8')
    print(html)

if __name__ == '__main__':
    main()
