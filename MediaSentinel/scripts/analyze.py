#!/usr/bin/env python3
"""
analyze.py
Sends collected media stack data to a local Ollama model and returns
a structured diagnosis with issue identification and fix recommendations.
"""

import json
import sys
import requests
import datetime
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"
PROMPT_PATH = Path(__file__).parent.parent / "prompts"

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def load_prompt(name):
    p = PROMPT_PATH / f"{name}.txt"
    if p.exists():
        return p.read_text()
    return ""

def call_lmstudio(model, system_prompt, user_content, host, temperature=0.2):
    """Call LM Studio via its OpenAI-compatible REST API (/v1/chat/completions)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": 4096,
        "stream": False,
    }
    try:
        r = requests.post(
            f"{host}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], None
    except requests.exceptions.ConnectionError:
        return None, (
            f"LM Studio not reachable at {host} - "
            "check that the local server is running in LM Studio "
            "(Server tab -> Start Server) and that port 1234 is open on that machine."
        )
    except KeyError:
        return None, f"Unexpected response shape from LM Studio: {r.text[:300]}"
    except Exception as e:
        return None, str(e)


def build_analysis_prompt(raw_data: dict) -> str:
    """Build the structured user prompt from raw collected data."""
    ts = raw_data.get("collected_at", "unknown")
    ip = raw_data.get("server_ip", "unknown")
    
    sections = [
        f"=== MEDIA SERVER DIAGNOSTIC REPORT ===",
        f"Collected at: {ts}",
        f"Server IP: {ip}",
        "",
    ]

    for svc in raw_data.get("services", []):
        name = svc.get("service", "unknown").upper()
        sections.append(f"--- {name} ---")
        
        if svc.get("errors"):
            sections.append(f"  SERVICE ERRORS: {json.dumps(svc['errors'], indent=2)}")

        if name == "JELLYFIN":
            if svc.get("active_sessions"):
                sessions = svc["active_sessions"]
                sections.append(f"  Active sessions ({len(sessions)}):")
                for s in sessions:
                    sections.append(f"    - {s['user']} watching '{s['now_playing']}' via {s['play_method']} on {s['client']}")

            if svc.get("active_transcodes"):
                sections.append(f"  ACTIVE TRANSCODES ({len(svc['active_transcodes'])}):")
                for t in svc["active_transcodes"]:
                    fv  = t.get("file_video", {})
                    fa  = t.get("file_audio", {})
                    tt  = t.get("transcoding_to", {})
                    hw  = t.get("hw_accel_type", "none")
                    sections.append(f"    Stream: {t['user']} on {t['client']} watching '{t['now_playing']}'")
                    sections.append(f"    FILE VIDEO: {fv.get('codec','?').upper()} {fv.get('profile','?')} {fv.get('resolution','?')} {fv.get('bitrate_mbps','?')}Mbps HDR={fv.get('hdr_type','?')} {fv.get('bit_depth','?')}bit")
                    sections.append(f"    FILE AUDIO: {fa.get('codec','?').upper()} {fa.get('title','?')} {fa.get('channels','?')}ch layout={fa.get('layout','?')}")
                    sections.append(f"    TRANSCODING TO: Video={tt.get('video_codec','?').upper()} {tt.get('resolution','?')} {tt.get('bitrate_mbps','?')}Mbps | Audio={tt.get('audio_codec','?').upper()} {tt.get('audio_channels','?')}ch | HW={hw}")
                    sections.append(f"    TRANSCODE REASONS:")
                    for rd in t.get("reason_detail", t.get("transcode_reasons", [])):
                        sections.append(f"      - {rd}")
                    if t.get("direct_play_options"):
                        sections.append(f"    OPTIONS TO ELIMINATE TRANSCODE:")
                        for opt in t.get("direct_play_options", []):
                            sections.append(f"      - {opt}")
                    sections.append("")

            if svc.get("recent_errors"):
                sections.append(f"  Recent errors/warnings ({len(svc['recent_errors'])}):")
                for e in svc["recent_errors"][:10]:
                    sections.append(f"    [{e['severity']}] {e['date']}: {e['name']} - {e['overview']}")

        elif name == "SONARR":
            missing = svc.get("missing_episodes", [])
            if missing:
                sections.append(f"  Missing episodes ({len(missing)}):")
                for ep in missing[:10]:
                    sections.append(f"    - {ep['series']} S{ep['season']:02d}E{ep['episode']:02d}: {ep['title']} (aired {ep['air_date']})")
                if len(missing) > 10:
                    sections.append(f"    ... and {len(missing)-10} more")
            
            for issue in svc.get("health_issues", []):
                sections.append(f"  [W] Health: [{issue['type']}] {issue['message']}")
            
            for q in svc.get("download_queue", []):
                if q.get("error"):
                    sections.append(f"  [W] Queue error: {q['series']} - {q['error']}")
            
            for d in svc.get("disk_space", []):
                flag = "[!]" if d["used_pct"] > 85 else "   "
                sections.append(f"  {flag} Disk {d['path']}: {d['used_pct']}% used ({d['free_gb']} GB free / {d['total_gb']} GB total)")

        elif name == "RADARR":
            missing = svc.get("missing_movies", [])
            if missing:
                sections.append(f"  Missing monitored movies ({len(missing)}):")
                for m in missing[:10]:
                    sections.append(f"    - {m['title']} ({m['year']}) - status: {m['status']}")
                if len(missing) > 10:
                    sections.append(f"    ... and {len(missing)-10} more")
            
            for issue in svc.get("health_issues", []):
                sections.append(f"  [W] Health: [{issue['type']}] {issue['message']}")
            
            for q in svc.get("download_queue", []):
                if q.get("error"):
                    sections.append(f"  [W] Queue error: {q['movie']} - {q['error']}")

        elif name == "JELLYSEER":
            not_in_lib  = svc.get("not_in_library", [])
            total       = svc.get("total_requests", 0)
            connected   = svc.get("connected", False)
            if not connected:
                sections.append(f"  CONNECTION FAILED: {svc.get('errors', [{}])[0].get('error', 'unknown')}")
            elif not_in_lib:
                failed      = [i for i in not_in_lib if i.get("status") == "FAILED"]
                pending     = [i for i in not_in_lib if i.get("status") == "PENDING"]
                in_progress = [i for i in not_in_lib if i.get("status") in ("APPROVED", "PROCESSING")]
                sections.append(f"  INFORMATIONAL - {len(not_in_lib)} of {total} requests not yet in library (normal for recent requests):")
                if failed:
                    sections.append(f"  ACTION NEEDED - {len(failed)} FAILED request(s):")
                    for item in failed:
                        year = f" ({item['year']})" if item.get('year') else ""
                        sections.append(f"    [FAILED] {item['type']} - {item['title']}{year} by {item['requested_by']} on {item['created']}")
                if pending:
                    sections.append(f"  PENDING APPROVAL - {len(pending)} request(s):")
                    for item in pending:
                        year = f" ({item['year']})" if item.get('year') else ""
                        sections.append(f"    [PENDING] {item['type']} - {item['title']}{year} by {item['requested_by']} on {item['created']}")
                if in_progress:
                    sections.append(f"  IN PROGRESS - {len(in_progress)} item(s) approved and downloading (no action needed):")
                    for item in in_progress:
                        year = f" ({item['year']})" if item.get('year') else ""
                        sections.append(f"    [{item['status']}] {item['type']} - {item['title']}{year}")
            else:
                sections.append(f"  All {total} requested items are available in the library.")

        sections.append("")

    # System
    sys_data  = raw_data.get("system", {})
    cfg_data  = raw_data.get("config_thresholds", {})
    free_good = cfg_data.get("disk_free_good_gb",     2000)
    free_warn = cfg_data.get("disk_free_warning_gb",  1500)
    free_crit = cfg_data.get("disk_free_critical_gb", 1000)
    monitored = cfg_data.get("monitored_drives",      ["K:"])

    if sys_data:
        sections.append("--- SYSTEM RESOURCES ---")
        sections.append(f"  CPU: {sys_data.get('cpu_percent', '?')}%  |  RAM: {sys_data.get('memory_percent', '?')}% ({sys_data.get('memory_used_gb','?')} / {sys_data.get('memory_total_gb','?')} GB)")
        sections.append("  Disk (K: DrivePool - primary media storage):")
        for d in sys_data.get("disks", []):
            mp = d["mountpoint"].rstrip("\\").rstrip("/")
            # Only report on monitored drives with free-space thresholds
            if any(mp.upper() == m.rstrip("\\").upper() for m in monitored):
                free_gb = d["free_gb"]
                if free_gb < free_crit:
                    flag = "CRITICAL"
                elif free_gb < free_warn:
                    flag = "WARNING"
                elif free_gb < free_good:
                    flag = "LOW"
                else:
                    flag = "OK"
                sections.append(f"  [{flag}] {d['mountpoint']} {free_gb} GB free of {d['total_gb']} GB ({d['used_pct']}% used)")

    return "\n".join(sections)


SYSTEM_PROMPT = """You are MediaSentinel, an expert AI systems analyst for self-hosted media servers.
Your stack: Jellyfin (media server + plugins), Sonarr (TV), Radarr (movies), Jellyseer (requests).

Your job:
1. Parse the diagnostic report provided.
2. Identify ALL issues: errors, missing files, playback problems, transcoding load, disk pressure, service failures.
3. For each issue, provide a clear diagnosis and ACTIONABLE fix steps.
4. Prioritise by severity: CRITICAL > WARNING > INFO.
5. Suggest proactive improvements where relevant.

Output format (strict JSON):
{
  "summary": "One-sentence overall health status",
  "health_score": 0-100,
  "issues": [
    {
      "id": "unique-slug",
      "severity": "CRITICAL|WARNING|INFO",
      "service": "jellyfin|sonarr|radarr|jellyseer|system",
      "category": "playback|transcode|missing_file|disk|download|service|config|performance",
      "title": "Short issue title",
      "description": "What is happening and why it matters",
      "fix_steps": ["Step 1", "Step 2", "..."],
      "fix_type": "manual|automated|monitor",
      "automation_script": "optional bash/python one-liner if automatable"
    }
  ],
  "transcode_analysis": {
    "active_count": 0,
    "hw_acceleration_in_use": true,
    "bottleneck_risk": "low|medium|high",
    "recommendations": []
  },
  "disk_analysis": {
    "pressure_level": "ok|warning|critical",
    "notes": ""
  },
  "proactive_suggestions": ["..."]
}

Return ONLY valid JSON. No preamble, no markdown fences."""


def main():
    cfg = load_config()
    
    # Read raw data from stdin or file arg
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            raw_data = json.load(f)
    else:
        raw_data = json.load(sys.stdin)

    user_prompt = build_analysis_prompt(raw_data)
    
    model   = cfg["server"]["local_ai_model"]
    host    = cfg["server"]["local_ai_host"]
    backend = cfg["server"].get("ai_backend", "lmstudio")

    print(f"[MediaSentinel] Sending data to {model} via LM Studio at {host}...", file=sys.stderr)

    result, err = call_lmstudio(model, SYSTEM_PROMPT, user_prompt, host)
    
    if err:
        print(json.dumps({"error": err, "raw_prompt": user_prompt[:500]}), file=sys.stdout)
        sys.exit(1)
    
    # Try to parse JSON from model output
    try:
        # Strip any accidental markdown fences
        clean = result.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])
        analysis = json.loads(clean)
        print(json.dumps(analysis, indent=2))
    except json.JSONDecodeError:
        # Return raw if model didn't respect JSON format
        print(json.dumps({"raw_analysis": result, "parse_error": "Model did not return valid JSON"}))


if __name__ == "__main__":
    main()
