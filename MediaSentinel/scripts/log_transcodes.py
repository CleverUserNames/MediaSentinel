#!/usr/bin/env python3
"""
log_transcodes.py
Reads active transcode sessions from the latest data JSON
and logs them to a persistent SQLite database.
Run after every collect_data.py call.

Usage: python log_transcodes.py <data.json>
"""

import json
import sys
import sqlite3
import datetime
from pathlib import Path

DB_PATH  = Path(__file__).parent.parent / "logs" / "transcodes.db"
KEEP_DAYS = 180

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    return conn

def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transcode_sessions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_key         TEXT NOT NULL,          -- dedupe key: title+user+client+date
            timestamp           TEXT NOT NULL,
            date                TEXT NOT NULL,           -- YYYY-MM-DD for grouping
            title               TEXT,
            media_type          TEXT,                    -- Movie or Episode
            user                TEXT,
            client              TEXT,
            device              TEXT,
            play_method         TEXT,
            -- Source file details
            src_video_codec     TEXT,
            src_video_profile   TEXT,
            src_resolution      TEXT,
            src_bitrate_mbps    REAL,
            src_hdr_type        TEXT,
            src_bit_depth       INTEGER,
            src_audio_codec     TEXT,
            src_audio_profile   TEXT,
            src_audio_channels  INTEGER,
            -- Transcode target
            dst_video_codec     TEXT,
            dst_resolution      TEXT,
            dst_bitrate_mbps    REAL,
            dst_audio_codec     TEXT,
            dst_audio_channels  INTEGER,
            -- Why transcoding
            transcode_reasons   TEXT,                    -- JSON array
            hw_accel_type       TEXT,
            hw_accel_active     INTEGER,                 -- 0 or 1
            -- Fix options collected
            direct_play_options TEXT,                    -- JSON array
            UNIQUE(session_key)
        );

        CREATE INDEX IF NOT EXISTS idx_date      ON transcode_sessions(date);
        CREATE INDEX IF NOT EXISTS idx_title     ON transcode_sessions(title);
        CREATE INDEX IF NOT EXISTS idx_timestamp ON transcode_sessions(timestamp);

        CREATE TABLE IF NOT EXISTS weekly_reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start  TEXT NOT NULL,
            week_end    TEXT NOT NULL,
            generated   TEXT NOT NULL,
            analysis    TEXT,           -- full AI analysis JSON
            html_path   TEXT
        );
    """)
    conn.commit()

def log_sessions(conn, data):
    """Extract active transcodes from data payload and insert into DB."""
    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")
    ts_str   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    jf = next((s for s in data.get("services", []) if s.get("service") == "jellyfin"), {})
    transcodes = jf.get("active_transcodes", [])

    inserted = 0
    skipped  = 0

    for t in transcodes:
        title  = t.get("now_playing") or "Unknown"
        user   = t.get("user") or "unknown"
        client = t.get("client") or "unknown"

        # Dedupe key: same title+user+client on same day = one record
        session_key = f"{date_str}|{title}|{user}|{client}"

        fv = t.get("file_video", {}) or {}
        fa = t.get("file_audio", {}) or {}
        tt = t.get("transcoding_to", {}) or {}

        reasons = t.get("transcode_reasons", [])
        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except Exception:
                reasons = [reasons]

        options = t.get("direct_play_options", [])
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except Exception:
                options = [options]

        try:
            conn.execute("""
                INSERT OR IGNORE INTO transcode_sessions (
                    session_key, timestamp, date, title, media_type,
                    user, client, device, play_method,
                    src_video_codec, src_video_profile, src_resolution,
                    src_bitrate_mbps, src_hdr_type, src_bit_depth,
                    src_audio_codec, src_audio_profile, src_audio_channels,
                    dst_video_codec, dst_resolution, dst_bitrate_mbps,
                    dst_audio_codec, dst_audio_channels,
                    transcode_reasons, hw_accel_type, hw_accel_active,
                    direct_play_options
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                session_key, ts_str, date_str,
                title,
                t.get("media_type"),
                user, client,
                t.get("device"),
                t.get("play_method"),
                fv.get("codec"),
                fv.get("profile"),
                fv.get("resolution"),
                fv.get("bitrate_mbps"),
                fv.get("hdr_type"),
                fv.get("bit_depth"),
                fa.get("codec"),
                fa.get("profile"),
                fa.get("channels"),
                tt.get("video_codec"),
                tt.get("resolution"),
                tt.get("bitrate_mbps"),
                tt.get("audio_codec"),
                tt.get("audio_channels"),
                json.dumps(reasons),
                t.get("hw_accel_type"),
                1 if t.get("hw_accel_active") else 0,
                json.dumps(options),
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  Warning: could not log transcode for '{title}': {e}", file=sys.stderr)

    conn.commit()
    return inserted, skipped

def cleanup_old_records(conn):
    """Remove records older than KEEP_DAYS."""
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    cur = conn.execute("DELETE FROM transcode_sessions WHERE date < ?", (cutoff,))
    conn.commit()
    return cur.rowcount

def main():
    if len(sys.argv) < 2:
        print("Usage: log_transcodes.py <data.json>", file=sys.stderr)
        sys.exit(1)

    data_file = sys.argv[1]
    try:
        data = json.load(open(data_file, encoding="utf-8"))
    except Exception as e:
        print(f"Error reading data file: {e}", file=sys.stderr)
        sys.exit(1)

    conn = get_db()
    inserted, skipped = log_sessions(conn, data)
    cleaned = cleanup_old_records(conn)

    # Print summary
    total = conn.execute("SELECT COUNT(*) FROM transcode_sessions").fetchone()[0]
    print(f"Transcodes logged: +{inserted} new, {skipped} duplicate, {cleaned} expired | Total in DB: {total}")
    conn.close()

if __name__ == "__main__":
    main()
