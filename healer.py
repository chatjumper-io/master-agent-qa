"""Auto-healer: fix simple issues via ADB commands."""
import time
from capture import _adb


def heal_device(serial, analysis):
    """Attempt to fix the device based on vision analysis.
    Returns dict with actions taken."""
    action = analysis.get("recommended_action", "none")
    result = {"serial": serial, "action_requested": action, "actions_taken": [], "success": False}

    if action == "none":
        result["success"] = True
        return result

    if action == "wake_screen":
        _adb(["shell", "input", "keyevent", "KEYCODE_WAKEUP"], serial=serial)
        time.sleep(1)
        _adb(["shell", "input", "keyevent", "KEYCODE_MENU"], serial=serial)
        # Swipe up to dismiss lock screen
        _adb(["shell", "input", "swipe", "540", "1800", "540", "800", "300"], serial=serial)
        result["actions_taken"].append("wake_screen + swipe_unlock")
        result["success"] = True

    elif action == "press_back":
        for _ in range(3):
            _adb(["shell", "input", "keyevent", "KEYCODE_BACK"], serial=serial)
            time.sleep(0.5)
        result["actions_taken"].append("press_back x3")
        result["success"] = True

    elif action == "dismiss_popup":
        # Try tapping common dismiss buttons
        # "Not Now" / "OK" / "Cancel" / "Got it" positions vary
        # Use UI dump to find them, or try common coordinates
        _adb(["shell", "input", "keyevent", "KEYCODE_BACK"], serial=serial)
        time.sleep(1)
        result["actions_taken"].append("back_to_dismiss")
        result["success"] = True

    elif action == "relaunch_app":
        # Get the IG packages, force-stop and relaunch main
        out, _, _ = _adb(["shell", "pm", "list", "packages"], serial=serial)
        ig_pkgs = [p.replace("package:", "") for p in out.splitlines() if "instagram" in p.lower()]
        
        if ig_pkgs:
            pkg = ig_pkgs[0]  # Use first (main) package
            _adb(["shell", "am", "force-stop", pkg], serial=serial)
            time.sleep(2)
            _adb(["shell", "am", "start", "-n",
                  f"{pkg}/com.instagram.mainactivity.LaunchActivity"], serial=serial)
            time.sleep(5)
            result["actions_taken"].append(f"force_stop + relaunch {pkg}")
            result["success"] = True
        else:
            result["actions_taken"].append("no_ig_package_found")

    elif action == "go_home_and_relaunch":
        _adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial=serial)
        time.sleep(2)
        # Find and launch IG
        out, _, _ = _adb(["shell", "pm", "list", "packages"], serial=serial)
        ig_pkgs = [p.replace("package:", "") for p in out.splitlines() if "instagram" in p.lower()]
        if ig_pkgs:
            pkg = ig_pkgs[0]
            _adb(["shell", "am", "start", "-n",
                  f"{pkg}/com.instagram.mainactivity.LaunchActivity"], serial=serial)
            time.sleep(5)
            result["actions_taken"].append(f"home + launch {pkg}")
            result["success"] = True

    elif action in ("needs_human", "relogin_required"):
        result["actions_taken"].append(f"flagged_for_human: {action}")
        result["success"] = False

    return result
