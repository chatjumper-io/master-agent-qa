"""
QA Phase 1 - Foundation Tests
Standalone QA scanner for the Instagram bot farm.
Does NOT modify master-agent. Read-only testing.
Usage: py qa_scan.py
"""
import sys
import os
import json
import time
import subprocess

# ---------------------------------------------------------------------------
# Resolve master-agent location (sibling folder)
# ---------------------------------------------------------------------------
QA_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_AGENT_DIR = os.path.join(os.path.dirname(QA_DIR), "master-agent")
if not os.path.isdir(MASTER_AGENT_DIR):
    print(f"ERROR: master-agent not found at {MASTER_AGENT_DIR}")
    print("Place this repo next to master-agent:")
    print("  ADB BOT/master-agent/")
    print("  ADB BOT/master-agent-qa/")
    sys.exit(1)

# Import master-agent settings (read-only)
sys.path.insert(0, MASTER_AGENT_DIR)
from config.settings import ADB_PATH, IS_WINDOWS

REPORT_PATH = os.path.join(QA_DIR, "qa_report.json")


def adb_cmd(args, timeout=15):
    """Run an ADB command and return stdout."""
    cmd = [ADB_PATH] + args
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", **kwargs,
        )
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_all_devices():
    """Return list of connected device serials."""
    output = adb_cmd(["devices"])
    devices = []
    for line in output.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


# ── Per-device tests ─────────────────────────────────────────────────────────

def test_device_basic(serial):
    """ADB connectivity, battery, foreground app, IG packages."""
    t = {}

    # ADB echo
    t["adb_ok"] = adb_cmd(["-s", serial, "shell", "echo", "OK"]).strip() == "OK"

    # Battery
    out = adb_cmd(["-s", serial, "shell", "dumpsys", "battery"])
    batt = None
    for ln in out.splitlines():
        if "level:" in ln:
            try:
                batt = int(ln.split("level:")[1].strip())
            except ValueError:
                pass
    t["battery"] = batt
    t["battery_ok"] = batt is not None and batt > 10

    # Foreground window
    out = adb_cmd(["-s", serial, "shell", "dumpsys", "window", "windows"])
    fg = "unknown"
    for ln in out.splitlines():
        if "mCurrentFocus" in ln or "mFocusedApp" in ln:
            fg = ln.strip()
            break
    t["foreground"] = fg
    t["ig_in_foreground"] = "instagram" in fg.lower()

    # Instagram packages installed
    out = adb_cmd(["-s", serial, "shell", "pm", "list", "packages"])
    pkgs = [p.replace("package:", "") for p in out.splitlines() if "instagram" in p.lower()]
    t["ig_packages"] = pkgs
    t["ig_count"] = len(pkgs)

    return t


def test_screen_id(serial):
    """Identify what screen the phone is showing."""
    out = adb_cmd(["-s", serial, "shell", "dumpsys", "window"])
    focus = "unknown"
    for ln in out.splitlines():
        if "mCurrentFocus" in ln:
            focus = ln.strip()
            break

    fl = focus.lower()
    if "instagram" in fl:
        if "maintab" in fl or "launchtab" in fl:
            screen = "ig_home"
        elif "directthread" in fl:
            screen = "ig_dm"
        elif "mediapicker" in fl:
            screen = "ig_post"
        elif "camera" in fl:
            screen = "ig_camera"
        else:
            screen = "ig_other"
    elif "launcher" in fl or "homescreen" in fl or "nexuslauncher" in fl:
        screen = "home_screen"
    elif "settings" in fl:
        screen = "settings"
    elif "getscreen" in fl:
        screen = "getscreen"
    else:
        screen = "other"

    return {"focus": focus, "screen": screen}


def test_ui_dump(serial):
    """Test uiautomator dump (the #1 known bug)."""
    for remote in ("/sdcard/ui_dump.xml", "/data/local/tmp/ui_dump.xml"):
        out = adb_cmd(
            ["-s", serial, "shell",
             f"uiautomator dump --compressed {remote} >/dev/null 2>&1 && cat {remote} && rm {remote}"],
            timeout=10,
        )
        if out and "<hierarchy" in out:
            count = out.count("<node ")
            return {"ok": True, "elements": count, "path": remote}

    return {"ok": False, "elements": 0, "error": "dump failed (likely video playing)"}


def test_brain_reachable(serial):
    """Check if the brain backend API is reachable from the phone."""
    # Try curl from the phone itself
    out = adb_cmd(
        ["-s", serial, "shell",
         "curl -s -o /dev/null -w '%{http_code}' --max-time 5 'http://172.86.111.99/API_db/brain/brain.php?action=ping'"],
        timeout=10,
    )
    return {"reachable": out.strip() in ("200", "404", "400"), "http_code": out.strip()}


# ── Main scan ────────────────────────────────────────────────────────────────

def run_scan():
    print("=" * 60)
    print("  QA Phase 1 - Foundation Scan")
    print("  master-agent-qa (read-only)")
    print("=" * 60)

    devices = get_all_devices()
    print(f"\nDevices connected: {len(devices)}\n")

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "adb_path": ADB_PATH,
        "device_count": len(devices),
        "devices": [],
        "summary": {"ok": 0, "warn": 0, "crit": 0},
    }

    for i, serial in enumerate(devices):
        print(f"[{i+1}/{len(devices)}] {serial} ", end="", flush=True)

        d = {"serial": serial}

        # Basic
        basic = test_device_basic(serial)
        d["basic"] = basic

        # Screen
        scr = test_screen_id(serial)
        d["screen"] = scr

        # UI dump (first 10 devices only to save time)
        if i < 10:
            ui = test_ui_dump(serial)
            d["ui_dump"] = ui
        else:
            d["ui_dump"] = {"skipped": True}

        # Brain (first 5 only)
        if i < 5:
            brain = test_brain_reachable(serial)
            d["brain"] = brain
        else:
            d["brain"] = {"skipped": True}

        # Status classification
        if not basic["adb_ok"]:
            status = "DEAD"
            report["summary"]["crit"] += 1
        elif not basic["battery_ok"]:
            status = "LOW_BATT"
            report["summary"]["crit"] += 1
        elif basic["ig_count"] == 0:
            status = "NO_IG"
            report["summary"]["crit"] += 1
        elif not basic["ig_in_foreground"]:
            status = "IG_NOT_FG"
            report["summary"]["warn"] += 1
        else:
            status = "OK"
            report["summary"]["ok"] += 1

        d["status"] = status

        # One-line output
        batt = basic.get("battery", "?")
        pkgs = basic.get("ig_count", 0)
        screen_name = scr.get("screen", "?")
        ui_ok = d.get("ui_dump", {}).get("ok", "skip")
        icons = {"OK": "OK", "IG_NOT_FG": "WARN", "LOW_BATT": "CRIT", "NO_IG": "CRIT", "DEAD": "DEAD"}
        tag = icons.get(status, "?")
        print(f"[{tag}] batt={batt}% pkgs={pkgs} screen={screen_name} ui_dump={ui_ok}")

        report["devices"].append(d)

    # Summary
    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"DONE  OK={s['ok']}  WARN={s['warn']}  CRIT={s['crit']}  TOTAL={len(devices)}")
    print(f"{'='*60}")

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved: {REPORT_PATH}")

    return report


if __name__ == "__main__":
    run_scan()
