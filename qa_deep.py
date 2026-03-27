"""
QA Deep Scan - Visual phone inspection via ADB screenshots.
Takes a screenshot of every phone, saves to screenshots/ folder,
correlates visual state with ADB data to find real bugs.

Usage: py qa_deep.py [--device SERIAL] [--limit N]
"""
import sys
import os
import json
import time
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
QA_DIR = Path(__file__).parent.resolve()
MASTER_AGENT_DIR = QA_DIR.parent / "master-agent"
if not MASTER_AGENT_DIR.is_dir():
    print(f"ERROR: master-agent not found at {MASTER_AGENT_DIR}")
    sys.exit(1)

sys.path.insert(0, str(MASTER_AGENT_DIR))
from config.settings import ADB_PATH, IS_WINDOWS

TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
SCREENSHOTS_DIR = QA_DIR / "screenshots" / TIMESTAMP
REPORT_PATH = QA_DIR / f"qa_deep_{TIMESTAMP}.json"


def adb(args, serial=None, timeout=15):
    """Run ADB command. Returns (stdout, stderr, returncode)."""
    cmd = [ADB_PATH]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    kw = {}
    if IS_WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           encoding="utf-8", errors="replace", **kw)
        return r.stdout.strip(), (r.stderr or "").strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1
    except Exception as e:
        return "", str(e), -1


def adb_raw(args, serial=None, timeout=15):
    """Run ADB returning raw bytes (for screencap)."""
    cmd = [ADB_PATH]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    kw = {}
    if IS_WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
        return r.stdout, r.returncode
    except Exception:
        return b"", -1


def get_devices():
    out, err, _ = adb(["devices"])
    if "daemon" in (err or ""):
        time.sleep(3)
        out, _, _ = adb(["devices"])
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == "device":
            devs.append(parts[0])
    return devs


# ── Screenshot capture ───────────────────────────────────────────────────────

def take_screenshot(serial, save_path):
    """Capture phone screen via ADB and save as PNG."""
    # Method 1: exec-out screencap (fastest, no temp file on phone)
    data, rc = adb_raw(["exec-out", "screencap", "-p"], serial=serial, timeout=10)
    if rc == 0 and len(data) > 1000:
        with open(save_path, "wb") as f:
            f.write(data)
        return True, len(data)

    # Method 2: screencap to phone, then pull
    adb(["shell", "screencap", "-p", "/sdcard/qa_screen.png"], serial=serial, timeout=10)
    pull_out, pull_err, pull_rc = adb(["pull", "/sdcard/qa_screen.png", str(save_path)], serial=serial, timeout=10)
    adb(["shell", "rm", "/sdcard/qa_screen.png"], serial=serial, timeout=5)
    if Path(save_path).exists() and Path(save_path).stat().st_size > 1000:
        return True, Path(save_path).stat().st_size
    
    return False, 0


# ── Deep device inspection ───────────────────────────────────────────────────

def inspect_device(serial):
    """Full deep inspection of one device."""
    result = {"serial": serial, "timestamp": time.strftime("%H:%M:%S")}

    # ── 1. Basic connectivity ──
    out, err, rc = adb(["shell", "echo", "ALIVE"], serial=serial)
    result["alive"] = "ALIVE" in out

    # ── 2. Battery ──
    out, _, _ = adb(["shell", "dumpsys", "battery"], serial=serial)
    batt = {}
    for ln in out.splitlines():
        ln = ln.strip()
        if "level:" in ln:
            try: batt["level"] = int(ln.split(":")[1].strip())
            except: pass
        if "temperature:" in ln:
            try: batt["temp_c"] = int(ln.split(":")[1].strip()) / 10
            except: pass
        if "status:" in ln:
            try: batt["charging"] = int(ln.split(":")[1].strip()) == 2
            except: pass
    result["battery"] = batt

    # ── 3. Screen state ──
    out, _, _ = adb(["shell", "dumpsys", "power"], serial=serial)
    result["screen_on"] = "mWakefulness=Awake" in out or "Display Power: state=ON" in out

    # ── 4. Current window / activity ──
    out, _, _ = adb(["shell", "dumpsys", "window"], serial=serial)
    focus = ""
    for ln in out.splitlines():
        if "mCurrentFocus" in ln:
            focus = ln.strip()
            break
    result["current_focus"] = focus

    out2, _, _ = adb(["shell", "dumpsys", "activity", "top"], serial=serial)
    top_activity = ""
    for ln in out2.splitlines()[:20]:
        if "ACTIVITY" in ln:
            top_activity = ln.strip()
            break
    result["top_activity"] = top_activity

    # ── 5. Classify screen ──
    fl = (focus + " " + top_activity).lower()
    if "instagram" in fl:
        if "maintab" in fl or "launchtab" in fl:
            screen = "ig_home"
        elif "directthread" in fl:
            screen = "ig_dm"
        elif "mediapicker" in fl or "gallery" in fl:
            screen = "ig_post_picker"
        elif "profile" in fl:
            screen = "ig_profile"
        elif "camera" in fl:
            screen = "ig_camera"
        elif "login" in fl or "signup" in fl:
            screen = "ig_login"
        elif "challenge" in fl or "checkpoint" in fl:
            screen = "ig_challenge"
        else:
            screen = "ig_other"
    elif "launcher" in fl or "homescreen" in fl:
        screen = "phone_home"
    elif "settings" in fl:
        screen = "phone_settings"
    elif "getscreen" in fl:
        screen = "getscreen_app"
    elif "automagic" in fl or "automate" in fl:
        screen = "automagic"
    elif "chrome" in fl or "browser" in fl:
        screen = "browser"
    elif "systemui" in fl and not any(x in fl for x in ["instagram", "launcher"]):
        screen = "system_dialog"
    else:
        screen = "unknown"
    result["screen"] = screen

    # ── 6. Instagram packages installed ──
    out, _, _ = adb(["shell", "pm", "list", "packages"], serial=serial)
    ig_pkgs = sorted([p.replace("package:", "") for p in out.splitlines()
                      if "instagram" in p.lower()])
    result["ig_packages"] = ig_pkgs
    result["ig_count"] = len(ig_pkgs)

    # ── 7. Which IG package is in foreground? ──
    fg_pkg = "none"
    for pkg in ig_pkgs:
        if pkg.lower() in fl:
            fg_pkg = pkg
            break
    result["ig_foreground_pkg"] = fg_pkg

    # ── 8. UI dump test ──
    ui_ok = False
    ui_elements = 0
    for remote in ("/sdcard/qa_ui.xml", "/data/local/tmp/qa_ui.xml"):
        out, _, _ = adb(
            ["shell", f"uiautomator dump --compressed {remote} >/dev/null 2>&1 && cat {remote} && rm {remote}"],
            serial=serial, timeout=10,
        )
        if out and "<hierarchy" in out:
            ui_ok = True
            ui_elements = out.count("<node ")
            # Extract visible text elements for context
            import re
            texts = re.findall(r'text="([^"]+)"', out)
            texts = [t for t in texts if t.strip()]
            result["ui_texts"] = texts[:30]  # first 30 non-empty texts
            break
    result["ui_dump_ok"] = ui_ok
    result["ui_elements"] = ui_elements

    # ── 9. Check for common popup/dialog texts ──
    popup_indicators = []
    ui_texts_lower = " ".join(result.get("ui_texts", [])).lower()
    if "try again later" in ui_texts_lower or "action blocked" in ui_texts_lower:
        popup_indicators.append("ACTION_BLOCKED")
    if "challenge" in ui_texts_lower or "verify" in ui_texts_lower:
        popup_indicators.append("VERIFICATION_CHALLENGE")
    if "log in" in ui_texts_lower or "sign up" in ui_texts_lower:
        popup_indicators.append("LOGGED_OUT")
    if "allow" in ui_texts_lower and "access" in ui_texts_lower:
        popup_indicators.append("PERMISSION_DIALOG")
    if "not now" in ui_texts_lower:
        popup_indicators.append("DISMISSABLE_DIALOG")
    if "close app" in ui_texts_lower or "keeps stopping" in ui_texts_lower:
        popup_indicators.append("APP_CRASH")
    if "update" in ui_texts_lower and ("available" in ui_texts_lower or "now" in ui_texts_lower):
        popup_indicators.append("UPDATE_PROMPT")
    result["popup_indicators"] = popup_indicators

    # ── 10. Network check ──
    out, _, _ = adb(["shell", "dumpsys", "connectivity"], serial=serial, timeout=10)
    has_wifi = "WIFI" in out and "CONNECTED" in out
    has_mobile = "MOBILE" in out and "CONNECTED" in out
    airplane = False
    out2, _, _ = adb(["shell", "settings", "get", "global", "airplane_mode_on"], serial=serial)
    if out2.strip() == "1":
        airplane = True
    result["network"] = {
        "wifi": has_wifi,
        "mobile": has_mobile,
        "airplane_mode": airplane,
        "connected": has_wifi or has_mobile,
    }

    # ── 11. Screenshot ──
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ss_path = SCREENSHOTS_DIR / f"{serial}.png"
    ss_ok, ss_size = take_screenshot(serial, ss_path)
    result["screenshot"] = {
        "captured": ss_ok,
        "path": str(ss_path) if ss_ok else None,
        "size_kb": round(ss_size / 1024, 1) if ss_ok else 0,
    }

    # ── 12. Bug classification ──
    bugs = []
    
    if not result["alive"]:
        bugs.append({"severity": "CRITICAL", "bug": "DEVICE_DEAD", "desc": "ADB not responding"})
    
    if not result.get("screen_on"):
        bugs.append({"severity": "HIGH", "bug": "SCREEN_OFF", "desc": "Screen is off - phone sleeping"})
    
    batt_level = batt.get("level")
    if batt_level is not None and batt_level < 15:
        bugs.append({"severity": "HIGH", "bug": "LOW_BATTERY", "desc": f"Battery at {batt_level}%"})
    
    batt_temp = batt.get("temp_c")
    if batt_temp is not None and batt_temp > 42:
        bugs.append({"severity": "HIGH", "bug": "OVERHEATING", "desc": f"Battery temp {batt_temp}C"})

    if not result["network"]["connected"]:
        bugs.append({"severity": "CRITICAL", "bug": "NO_NETWORK", "desc": "No wifi or mobile data"})
    
    if result["network"]["airplane_mode"]:
        bugs.append({"severity": "HIGH", "bug": "AIRPLANE_MODE", "desc": "Airplane mode stuck ON"})

    if result["ig_count"] == 0:
        bugs.append({"severity": "CRITICAL", "bug": "NO_INSTAGRAM", "desc": "No Instagram packages installed"})

    if screen == "phone_home":
        bugs.append({"severity": "HIGH", "bug": "ON_HOME_SCREEN", "desc": "Phone on home screen, not Instagram"})
    elif screen == "phone_settings":
        bugs.append({"severity": "MEDIUM", "bug": "STUCK_IN_SETTINGS", "desc": "Phone stuck in settings app"})
    elif screen == "ig_login":
        bugs.append({"severity": "CRITICAL", "bug": "LOGGED_OUT", "desc": "Instagram showing login screen"})
    elif screen == "ig_challenge":
        bugs.append({"severity": "CRITICAL", "bug": "CHALLENGE_REQUIRED", "desc": "Instagram verification challenge"})
    elif screen == "unknown":
        bugs.append({"severity": "MEDIUM", "bug": "UNKNOWN_SCREEN", "desc": f"Unrecognized screen: {focus[:80]}"})

    if not ui_ok:
        bugs.append({"severity": "HIGH", "bug": "UI_DUMP_FAILED", "desc": "uiautomator dump returned empty (video playing?)"})

    if "ACTION_BLOCKED" in popup_indicators:
        bugs.append({"severity": "CRITICAL", "bug": "ACTION_BLOCKED", "desc": "Instagram action block dialog visible"})
    if "APP_CRASH" in popup_indicators:
        bugs.append({"severity": "CRITICAL", "bug": "APP_CRASHED", "desc": "App crash dialog visible"})
    if "LOGGED_OUT" in popup_indicators:
        bugs.append({"severity": "CRITICAL", "bug": "LOGGED_OUT_UI", "desc": "Login/signup text detected in UI"})
    if "PERMISSION_DIALOG" in popup_indicators:
        bugs.append({"severity": "LOW", "bug": "PERMISSION_DIALOG", "desc": "Permission dialog needs dismissing"})
    if "UPDATE_PROMPT" in popup_indicators:
        bugs.append({"severity": "LOW", "bug": "UPDATE_PROMPT", "desc": "App update dialog visible"})

    result["bugs"] = bugs
    
    # Overall status
    severities = [b["severity"] for b in bugs]
    if "CRITICAL" in severities:
        result["status"] = "CRITICAL"
    elif "HIGH" in severities:
        result["status"] = "BROKEN"
    elif "MEDIUM" in severities:
        result["status"] = "WARN"
    elif "LOW" in severities:
        result["status"] = "MINOR"
    else:
        result["status"] = "OK"

    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="QA Deep Scan")
    parser.add_argument("--device", "-d", help="Test single device serial")
    parser.add_argument("--limit", "-l", type=int, help="Max devices to scan")
    args = parser.parse_args()

    print("=" * 70)
    print("  QA DEEP SCAN - Visual Phone Inspection")
    print(f"  Screenshots: {SCREENSHOTS_DIR}")
    print(f"  Report: {REPORT_PATH}")
    print("=" * 70)

    if args.device:
        devices = [args.device]
    else:
        devices = get_devices()

    if args.limit:
        devices = devices[:args.limit]

    print(f"\nScanning {len(devices)} devices...\n")

    report = {
        "timestamp": TIMESTAMP,
        "device_count": len(devices),
        "screenshots_dir": str(SCREENSHOTS_DIR),
        "devices": [],
        "summary": {"OK": 0, "MINOR": 0, "WARN": 0, "BROKEN": 0, "CRITICAL": 0},
        "all_bugs": [],
    }

    for i, serial in enumerate(devices):
        print(f"[{i+1}/{len(devices)}] {serial} ", end="", flush=True)
        
        d = inspect_device(serial)
        report["devices"].append(d)
        report["summary"][d["status"]] += 1
        
        # Collect bugs
        for bug in d.get("bugs", []):
            report["all_bugs"].append({"serial": serial, **bug})

        # Print one-line summary
        batt = d["battery"].get("level", "?")
        temp = d["battery"].get("temp_c", "?")
        scr = d["screen"]
        ss = "SS" if d["screenshot"]["captured"] else "NO_SS"
        ui = "UI_OK" if d["ui_dump_ok"] else "UI_FAIL"
        net = "NET" if d["network"]["connected"] else "NO_NET"
        bug_count = len(d["bugs"])
        status = d["status"]

        print(f"[{status}] batt={batt}%/{temp}C {scr} {ui} {net} {ss} bugs={bug_count}")
        
        if d["bugs"]:
            for bug in d["bugs"]:
                print(f"         {bug['severity']}: {bug['bug']} - {bug['desc']}")

    # ── Summary ──
    s = report["summary"]
    print(f"\n{'='*70}")
    print(f"RESULTS: OK={s['OK']} MINOR={s['MINOR']} WARN={s['WARN']} BROKEN={s['BROKEN']} CRIT={s['CRITICAL']}")
    print(f"Total bugs found: {len(report['all_bugs'])}")
    print(f"Screenshots saved: {SCREENSHOTS_DIR}")
    print(f"{'='*70}")

    # Bug breakdown
    if report["all_bugs"]:
        print("\nBUG BREAKDOWN:")
        from collections import Counter
        bug_types = Counter(b["bug"] for b in report["all_bugs"])
        for bug_type, count in bug_types.most_common():
            sev = next(b["severity"] for b in report["all_bugs"] if b["bug"] == bug_type)
            print(f"  [{sev}] {bug_type}: {count} devices")

    # Save report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nFull report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
