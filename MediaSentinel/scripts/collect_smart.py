#!/usr/bin/env python3
"""
collect_smart.py
Collects SMART health data from all drives using smartmontools.
Must be run as Administrator for full ATA SMART access.
Returns JSON array of drive health objects.
"""

import json
import subprocess
import re
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.json"

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def get_smartctl_path(cfg):
    return cfg.get("paths", {}).get(
        "smartctl",
        r"C:\Program Files\smartmontools\bin\smartctl.exe"
    )

def get_drives(cfg):
    drives = cfg.get("paths", {}).get("smart_drives")
    if drives:
        return drives
    # Default: scan sda-sdd as ATA, sde as NVMe
    return [
        {"dev": "/dev/sda", "type": "ata"},
        {"dev": "/dev/sdb", "type": "ata"},
        {"dev": "/dev/sdc", "type": "ata"},
        {"dev": "/dev/sdd", "type": "ata"},
        {"dev": "/dev/sde", "type": "nvme"},
    ]

ATTR_MAP = {
    "5":   "reallocated_sectors",
    "9":   "power_on_hours",
    "187": "uncorrectable_errors",
    "188": "command_timeout",
    "190": "airflow_temp_c",
    "194": "temperature_c",
    "196": "reallocated_events",
    "197": "pending_sectors",
    "198": "offline_uncorrectable",
    "199": "udma_crc_errors",
    "231": "ssd_life_left",
    "233": "media_wearout",
}


def run_smartctl(dev, dtype, smartctl_path):
    try:
        result = subprocess.run(
            [smartctl_path, "-a", dev, "-d", dtype],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout + result.stderr
    except FileNotFoundError:
        return f"ERROR: smartctl not found at {smartctl_path}"
    except subprocess.TimeoutExpired:
        return "ERROR: smartctl timed out"
    except Exception as e:
        return f"ERROR: {e}"


def parse_ata(output, dev):
    drive = {
        "device": dev, "type": "HDD/SSD",
        "model": "Unknown", "serial": "Unknown", "capacity": "Unknown",
        "health": "UNKNOWN", "temperature_c": None, "power_on_hours": None,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "offline_uncorrectable": 0, "reallocated_events": 0,
        "limited_access": False, "errors": [],
    }

    if "Limited functionality due to missing admin rights" in output:
        drive["limited_access"] = True
        drive["errors"].append("Run as Administrator for full SMART data")

    m = re.search(r"(?:Device Model|Model Family):\s+(.+)", output)
    if m:
        drive["model"] = m.group(1).strip()

    m = re.search(r"Serial Number:\s+(\S+)", output)
    if m:
        drive["serial"] = m.group(1).strip()

    m = re.search(r"User Capacity:\s+[\d,]+ bytes \[(.+?)\]", output)
    if m:
        drive["capacity"] = m.group(1).strip()

    m = re.search(r"SMART overall-health self-assessment test result:\s+(\w+)", output)
    if m:
        drive["health"] = m.group(1).strip()

    for line in output.splitlines():
        m = re.match(r"\s*(\d+)\s+\S+\s+0x\w+\s+\d+\s+\d+\s+\d+\s+\S+\s+\S+\s+\S+\s+(\S+.*)", line)
        if m:
            attr_id = m.group(1).strip()
            raw_val = m.group(2).strip()
            num_match = re.match(r"(\d+)", raw_val)
            if num_match:
                val = int(num_match.group(1))
                key = ATTR_MAP.get(attr_id)
                if key:
                    drive[key] = val

    if not drive.get("temperature_c") and drive.get("airflow_temp_c"):
        drive["temperature_c"] = drive["airflow_temp_c"]

    return drive


def parse_nvme(output, dev):
    drive = {
        "device": dev, "type": "NVMe SSD",
        "model": "Unknown", "serial": "Unknown", "capacity": "Unknown",
        "health": "UNKNOWN", "temperature_c": None, "power_on_hours": None,
        "reallocated_sectors": 0, "pending_sectors": 0,
        "offline_uncorrectable": 0, "data_read_tb": None,
        "data_written_tb": None, "critical_temp_time": 0,
        "life_used_pct": None, "errors": [],
    }

    m = re.search(r"Model Number:\s+(.+)", output)
    if m:
        drive["model"] = m.group(1).strip()

    m = re.search(r"Serial Number:\s+(\S+)", output)
    if m:
        drive["serial"] = m.group(1).strip()

    m = re.search(r"Total NVM Capacity:\s+[\d,]+ \[(.+?)\]", output)
    if m:
        drive["capacity"] = m.group(1).strip()

    m = re.search(r"SMART overall-health self-assessment test result:\s+(\w+)", output)
    if m:
        drive["health"] = m.group(1).strip()

    m = re.search(r"Temperature:\s+(\d+) Celsius", output)
    if m:
        drive["temperature_c"] = int(m.group(1))

    m = re.search(r"Power On Hours:\s+([\d,]+)", output)
    if m:
        drive["power_on_hours"] = int(m.group(1).replace(",", ""))

    m = re.search(r"Data Units Read:\s+[\d,]+ \[([\d.]+ \w+)\]", output)
    if m:
        drive["data_read_tb"] = m.group(1)

    m = re.search(r"Data Units Written:\s+[\d,]+ \[([\d.]+ \w+)\]", output)
    if m:
        drive["data_written_tb"] = m.group(1)

    m = re.search(r"Critical Comp\. Temperature Time:\s+(\d+)", output)
    if m:
        drive["critical_temp_time"] = int(m.group(1))

    m = re.search(r"Media and Data Integrity Errors:\s+(\d+)", output)
    if m:
        drive["offline_uncorrectable"] = int(m.group(1))

    m = re.search(r"Percentage Used:\s+(\d+)%", output)
    if m:
        drive["life_used_pct"] = int(m.group(1))

    return drive


def assess_health(drive):
    issues  = []
    severity = "OK"

    def escalate(s):
        order = ["OK", "INFO", "WARNING", "CRITICAL"]
        return s if order.index(s) > order.index(severity) else severity

    if drive.get("health") not in ("PASSED", "OK", "UNKNOWN"):
        issues.append(f"SMART health test: {drive.get('health')}")
        severity = "CRITICAL"

    realloc = drive.get("reallocated_sectors", 0) or 0
    if realloc > 10:
        issues.append(f"Reallocated sectors: {realloc} (CRITICAL)")
        severity = "CRITICAL"
    elif realloc > 0:
        issues.append(f"Reallocated sectors: {realloc} (WARNING)")
        severity = escalate("WARNING")

    pending = drive.get("pending_sectors", 0) or 0
    if pending > 0:
        issues.append(f"Pending sectors: {pending}")
        severity = escalate("WARNING")

    uncorr = drive.get("offline_uncorrectable", 0) or 0
    if uncorr > 0:
        issues.append(f"Uncorrectable errors: {uncorr}")
        severity = "CRITICAL"

    temp = drive.get("temperature_c")
    is_nvme = drive.get("type") == "NVMe SSD"
    warn_temp = 60 if is_nvme else 45
    crit_temp = 70 if is_nvme else 55
    if temp:
        if temp >= crit_temp:
            issues.append(f"Temperature critical: {temp}C")
            severity = "CRITICAL"
        elif temp >= warn_temp:
            issues.append(f"Temperature high: {temp}C")
            severity = escalate("WARNING")

    hours = drive.get("power_on_hours") or 0
    if hours > 60000:
        issues.append(f"Very high power-on hours: {hours:,}h - consider replacement")
        severity = escalate("WARNING")
    elif hours > 40000:
        issues.append(f"High power-on hours: {hours:,}h - monitor closely")
        severity = escalate("INFO")

    life = drive.get("life_used_pct")
    if life is not None:
        if life >= 90:
            issues.append(f"NVMe life used: {life}% - replace soon")
            severity = "CRITICAL"
        elif life >= 70:
            issues.append(f"NVMe life used: {life}% - plan replacement")
            severity = escalate("WARNING")

    drive["severity"] = severity
    drive["issues"]   = issues
    return drive


def main():
    cfg = load_config()
    smartctl_path = get_smartctl_path(cfg)
    drives = get_drives(cfg)

    if not Path(smartctl_path).exists():
        print(json.dumps({"error": f"smartctl not found at {smartctl_path}"}))
        sys.exit(1)

    results = []
    for d in drives:
        output = run_smartctl(d["dev"], d["type"], smartctl_path)
        if output.startswith("ERROR:") and "SMART" not in output:
            results.append({
                "device": d["dev"], "type": d["type"],
                "health": "UNREACHABLE", "severity": "INFO",
                "model": "Unknown", "serial": "Unknown",
                "errors": [output.strip()], "issues": []
            })
            continue

        if d["type"] == "nvme":
            drive = parse_nvme(output, d["dev"])
        else:
            drive = parse_ata(output, d["dev"])


        assess_health(drive)
        results.append(drive)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
