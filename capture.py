"""ADB screenshot capture and device data collection."""
import subprocess
import time
from pathlib import Path
from config import ADB_PATH, IS_WINDOWS


def _adb(args, serial=None, timeout=15):
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


def _adb_raw(args, serial=None, timeout=15):
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
    """List connected device serials."""
    out, err, _ = _adb(["devices"])
    if "daemon" in (err or ""):
        time.sleep(3)
        out, _, _ = _adb(["devices"])
    return [line.split("\t")[0] for line in out.splitlines()[1:]
            if "\tdevice" in line]


def take_screenshot(serial, save_path):
    """Capture phone screen as PNG. Returns (success, file_size)."""
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # Fast path: exec-out (no temp file on phone)
    data, rc = _adb_raw(["exec-out", "screencap", "-p"], serial=serial, timeout=10)
    if rc == 0 and len(data) > 1000:
        save_path.write_bytes(data)
        return True, len(data)

    # Fallback: screencap + pull
    _adb(["shell", "screencap", "-p", "/sdcard/_qa.png"], serial=serial, timeout=10)
    _adb(["pull", "/sdcard/_qa.png", str(save_path)], serial=serial, timeout=10)
    _adb(["shell", "rm", "/sdcard/_qa.png"], serial=serial, timeout=5)
    if save_path.exists() and save_path.stat().st_size > 1000:
        return True, save_path.stat().st_size

    return False, 0


def collect_device_data(serial):
    """Gather ADB metadata for a device."""
    data = {"serial": serial}

    # Foreground activity
    out, _, _ = _adb(["shell", "dumpsys", "activity", "top"], serial=serial, timeout=10)
    for ln in out.splitlines()[:20]:
        if "ACTIVITY" in ln:
            data["top_activity"] = ln.strip()
            break

    # Current window focus
    out, _, _ = _adb(["shell", "dumpsys", "window"], serial=serial, timeout=10)
    for ln in out.splitlines():
        if "mCurrentFocus" in ln:
            data["current_focus"] = ln.strip()
            break

    # IG packages
    out, _, _ = _adb(["shell", "pm", "list", "packages"], serial=serial)
    data["ig_packages"] = sorted(
        p.replace("package:", "") for p in out.splitlines() if "instagram" in p.lower()
    )

    # Network
    out, _, _ = _adb(["shell", "settings", "get", "global", "airplane_mode_on"], serial=serial)
    data["airplane_mode"] = out.strip() == "1"

    return data
