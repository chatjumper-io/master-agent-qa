"""
Phone inspector — captures screenshots + ADB data for anomaly diagnosis.
Called when an anomaly is detected to gather evidence.
"""
import json
import time
import subprocess
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass
class PhoneSnapshot:
    serial: str
    timestamp: str
    screenshot_path: Optional[str]
    ui_dump_xml: Optional[str]
    ui_texts: list
    top_activity: str
    current_focus: str
    screen_type: str
    ig_packages: list
    airplane_mode: bool
    log_tail: list  # last N log lines


def _adb(args, serial=None, adb_path="adb", timeout=15):
    cmd = [adb_path]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    kw = {"capture_output": True, "timeout": timeout, "encoding": "utf-8", "errors": "replace"}
    try:
        import platform
        if platform.system() == "Windows":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        r = subprocess.run(cmd, **kw)
        return r.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def _adb_raw(args, serial=None, adb_path="adb", timeout=15):
    cmd = [adb_path]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    try:
        import platform
        kw = {"capture_output": True, "timeout": timeout}
        if platform.system() == "Windows":
            kw["creationflags"] = 0x08000000
        r = subprocess.run(cmd, **kw)
        return r.stdout
    except Exception:
        return b""


def capture_screenshot(serial: str, save_dir: Path, adb_path: str = "adb") -> Optional[str]:
    """Take ADB screenshot. Returns path or None."""
    save_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%H%M%S")
    path = save_dir / f"{serial}_{ts}.png"

    data = _adb_raw(["exec-out", "screencap", "-p"], serial=serial, adb_path=adb_path, timeout=10)
    if data and len(data) > 1000:
        path.write_bytes(data)
        return str(path)
    return None


def capture_ui_dump(serial: str, adb_path: str = "adb") -> tuple[Optional[str], list]:
    """Capture UI hierarchy. Returns (xml_string, extracted_texts)."""
    for remote in ("/sdcard/_qa_ui.xml", "/data/local/tmp/_qa_ui.xml"):
        output = _adb(
            ["shell", f"uiautomator dump --compressed {remote} >/dev/null 2>&1 && cat {remote} && rm {remote}"],
            serial=serial, adb_path=adb_path, timeout=10,
        )
        if output and "<hierarchy" in output:
            texts = re.findall(r'text="([^"]+)"', output)
            texts = [t for t in texts if t.strip()]
            return output, texts[:50]
    return None, []


def identify_screen(focus: str, activity: str, ui_texts: list) -> str:
    """Classify what screen the phone is on."""
    combined = (focus + " " + activity).lower()
    texts_lower = " ".join(ui_texts).lower()

    if "instagram" in combined:
        if "maintab" in combined or "launchtab" in combined:
            if "home" in texts_lower:
                return "ig_feed"
            if "search" in texts_lower or "explore" in texts_lower:
                return "ig_explore"
            return "ig_main"
        if "directthread" in combined:
            return "ig_dm_thread"
        if "direct" in combined:
            return "ig_dm_inbox"
        if "mediapicker" in combined or "gallery" in combined:
            return "ig_media_picker"
        if "camera" in combined:
            return "ig_camera"
        if "login" in combined or "signup" in combined:
            return "ig_login"
        if "challenge" in combined or "checkpoint" in combined:
            return "ig_challenge"
        if any(x in texts_lower for x in ["action blocked", "try again later"]):
            return "ig_action_blocked"
        if any(x in texts_lower for x in ["log in", "sign up", "create new account"]):
            return "ig_logged_out"
        return "ig_other"
    elif "launcher" in combined or "homescreen" in combined:
        return "phone_home"
    elif "settings" in combined:
        if "imei" in texts_lower or "about phone" in texts_lower:
            return "settings_imei"
        return "phone_settings"
    elif "dialer" in combined or "phone" in combined:
        return "phone_dialer"
    elif "getscreen" in combined:
        return "getscreen"
    elif "automagic" in combined or "automate" in combined:
        return "automagic"
    elif "vending" in combined or "play store" in texts_lower:
        return "play_store"
    elif "chrome" in combined or "browser" in combined:
        return "browser"
    else:
        return "unknown"


def take_snapshot(serial: str, save_dir: Path, adb_path: str = "adb",
                  log_tail: list = None) -> PhoneSnapshot:
    """Full phone inspection — screenshot + ADB data + classification."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    # Screenshot
    ss_path = capture_screenshot(serial, save_dir, adb_path)

    # UI dump
    ui_xml, ui_texts = capture_ui_dump(serial, adb_path)

    # Activity info
    top_activity = ""
    out = _adb(["shell", "dumpsys", "activity", "top"], serial=serial, adb_path=adb_path, timeout=10)
    for ln in out.splitlines()[:20]:
        if "ACTIVITY" in ln:
            top_activity = ln.strip()
            break

    # Window focus
    current_focus = ""
    out = _adb(["shell", "dumpsys", "window"], serial=serial, adb_path=adb_path, timeout=10)
    for ln in out.splitlines():
        if "mCurrentFocus" in ln:
            current_focus = ln.strip()
            break

    # IG packages
    out = _adb(["shell", "pm", "list", "packages"], serial=serial, adb_path=adb_path)
    ig_packages = sorted(p.replace("package:", "") for p in out.splitlines() if "instagram" in p.lower())

    # Airplane mode
    out = _adb(["shell", "settings", "get", "global", "airplane_mode_on"], serial=serial, adb_path=adb_path)
    airplane = out.strip() == "1"

    # Screen classification
    screen_type = identify_screen(current_focus, top_activity, ui_texts)

    return PhoneSnapshot(
        serial=serial,
        timestamp=ts,
        screenshot_path=ss_path,
        ui_dump_xml=ui_xml[:500] if ui_xml else None,
        ui_texts=ui_texts,
        top_activity=top_activity,
        current_focus=current_focus,
        screen_type=screen_type,
        ig_packages=ig_packages,
        airplane_mode=airplane,
        log_tail=log_tail or [],
    )
