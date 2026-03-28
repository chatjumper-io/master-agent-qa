"""
QA Monitor Daemon — 24/7 monitoring loop for the phone farm.

Runs on Server35 alongside master-agent. Watches bot logs,
captures evidence on anomalies, files GitHub issues, reports fleet health.

Usage:
  py -m monitor.daemon                    # Run the monitoring loop
  py -m monitor.daemon --once             # Single pass (for testing)
  py -m monitor.daemon --interval 30      # Custom check interval
"""
import sys
import os
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter

# Add parent dir for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor.log_parser import FleetLogParser, Severity, Anomaly
from monitor.phone_inspector import take_snapshot
from monitor.issue_creator import GitHubIssueCreator

# ── Config ────────────────────────────────────────────────────────────────────

MASTER_AGENT_DIR = Path(__file__).parent.parent.parent / "master-agent"
QA_DIR = Path(__file__).parent.parent

# Try to get ADB path from master-agent config
try:
    sys.path.insert(0, str(MASTER_AGENT_DIR))
    from config.settings import ADB_PATH, ANTHROPIC_API_KEY
except ImportError:
    ADB_PATH = "adb"
    ANTHROPIC_API_KEY = ""

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
SNAPSHOTS_DIR = QA_DIR / "snapshots"
REPORTS_DIR = QA_DIR / "reports"
HEALTH_LOG = QA_DIR / "fleet_health.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(QA_DIR / "monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("qa-monitor")


# ── Device Discovery ──────────────────────────────────────────────────────────

def get_connected_devices() -> list[str]:
    """Get ADB device serials."""
    import subprocess
    try:
        kw = {"capture_output": True, "text": True, "timeout": 10, "encoding": "utf-8", "errors": "replace"}
        import platform
        if platform.system() == "Windows":
            kw["creationflags"] = 0x08000000
        r = subprocess.run([ADB_PATH, "devices"], **kw)
        devices = []
        for line in r.stdout.strip().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) == 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices
    except Exception as e:
        log.error(f"Failed to list devices: {e}")
        return []


# ── Log Collection ────────────────────────────────────────────────────────────

def read_bot_logs(device_serial: str, max_lines: int = 100) -> list[str]:
    """Read recent log lines for a device from the bot log file."""
    # The bot writes logs per device: bot_{SERIAL}.log
    log_patterns = [
        MASTER_AGENT_DIR / f"bot_{device_serial}.log",
        MASTER_AGENT_DIR / "logs" / f"bot_{device_serial}.log",
        MASTER_AGENT_DIR / f"logs/{device_serial}.log",
    ]

    for path in log_patterns:
        if path.exists():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                return lines[-max_lines:]
            except Exception:
                pass

    return []


def read_gui_log_output() -> list[tuple[str, str]]:
    """Read the GUI's combined log output if available.
    Returns list of (serial, line) tuples."""
    # The GUI tester writes to a combined log
    combined = MASTER_AGENT_DIR / "gui_output.log"
    if not combined.exists():
        return []

    try:
        lines = combined.read_text(encoding="utf-8", errors="replace").splitlines()
        results = []
        current_serial = "unknown"
        for line in lines[-500:]:
            # Try to extract serial from log format [SERIAL] or Device: SERIAL
            import re
            m = re.search(r'\b(R3CN\w{6,})\b', line)
            if m:
                current_serial = m.group(1)
            results.append((current_serial, line))
        return results
    except Exception:
        return []


# ── Anomaly Handling ──────────────────────────────────────────────────────────

def handle_anomaly(anomaly: Anomaly, fleet_parser: FleetLogParser,
                   issue_creator: GitHubIssueCreator = None):
    """Handle a detected anomaly — log, snapshot, optionally file issue."""

    sev = anomaly.severity.value.upper()
    log.warning(f"[{sev}] {anomaly.serial} | {anomaly.category}: {anomaly.message}")

    # For ERROR and CRITICAL, take a phone snapshot
    snapshot = None
    if anomaly.severity in (Severity.ERROR, Severity.CRITICAL):
        try:
            snap_dir = SNAPSHOTS_DIR / datetime.now().strftime("%Y%m%d")
            log_tail = read_bot_logs(anomaly.serial, max_lines=50)
            snapshot = take_snapshot(anomaly.serial, snap_dir, ADB_PATH, log_tail)
            log.info(f"  Snapshot: screen={snapshot.screen_type}, screenshot={snapshot.screenshot_path}")
        except Exception as e:
            log.error(f"  Failed to take snapshot: {e}")

    # File GitHub issue for CRITICAL
    if anomaly.severity == Severity.CRITICAL and issue_creator:
        try:
            result = issue_creator.create_anomaly_issue(anomaly, snapshot)
            if result.get("created"):
                log.info(f"  GitHub issue created: #{result['number']} {result.get('url', '')}")
            elif result.get("skipped"):
                log.info(f"  Issue skipped (duplicate within 1h)")
            else:
                log.warning(f"  Issue creation failed: {result}")
        except Exception as e:
            log.error(f"  Failed to create issue: {e}")


# ── Health Reporting ──────────────────────────────────────────────────────────

def generate_health_report(devices: list[str], fleet_parser: FleetLogParser) -> dict:
    """Generate fleet health summary."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "devices_connected": len(devices),
        "devices": {},
    }

    # Quick status check per device
    for serial in devices[:10]:  # Limit to avoid ADB overload
        try:
            from monitor.phone_inspector import _adb
            # Get foreground app
            focus = _adb(["shell", "dumpsys", "window"], serial=serial, adb_path=ADB_PATH, timeout=5)
            screen = "unknown"
            for ln in focus.splitlines():
                if "mCurrentFocus" in ln:
                    fl = ln.lower()
                    if "instagram" in fl:
                        screen = "instagram"
                    elif "launcher" in fl or "homescreen" in fl:
                        screen = "home_screen"
                    elif "settings" in fl:
                        screen = "settings"
                    else:
                        screen = "other"
                    break

            report["devices"][serial] = {"screen": screen}
        except Exception:
            report["devices"][serial] = {"screen": "error"}

    # Anomaly summary
    summary = fleet_parser.get_summary()
    report["anomaly_summary"] = summary

    # Count screens
    screens = Counter(d.get("screen", "?") for d in report["devices"].values())
    report["screen_distribution"] = dict(screens)

    return report


def log_health(report: dict):
    """Append health report to JSONL file."""
    HEALTH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(report, default=str) + "\n")


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run_monitor(interval: int = 30, single_pass: bool = False):
    """Main monitoring loop."""
    log.info("=" * 60)
    log.info("  QA Monitor Daemon Starting")
    log.info(f"  ADB: {ADB_PATH}")
    log.info(f"  Check interval: {interval}s")
    log.info(f"  GitHub issues: {'enabled' if GITHUB_TOKEN else 'disabled'}")
    log.info(f"  Snapshots: {SNAPSHOTS_DIR}")
    log.info("=" * 60)

    fleet_parser = FleetLogParser()
    issue_creator = GitHubIssueCreator(GITHUB_TOKEN) if GITHUB_TOKEN else None

    last_health_report = 0
    last_pattern_report = 0
    cycle = 0

    while True:
        cycle += 1
        cycle_start = time.time()

        try:
            # ── 1. Get connected devices ──
            devices = get_connected_devices()
            if cycle == 1 or cycle % 10 == 0:
                log.info(f"[Cycle {cycle}] {len(devices)} devices connected")

            # ── 2. Ingest log lines ──
            anomalies_this_cycle = []

            for serial in devices:
                lines = read_bot_logs(serial, max_lines=20)
                for line in lines:
                    anomaly = fleet_parser.ingest_line(serial, line)
                    if anomaly:
                        anomalies_this_cycle.append(anomaly)
                        handle_anomaly(anomaly, fleet_parser, issue_creator)

            # Also check GUI combined log
            gui_lines = read_gui_log_output()
            for serial, line in gui_lines[-50:]:
                anomaly = fleet_parser.ingest_line(serial, line)
                if anomaly:
                    anomalies_this_cycle.append(anomaly)
                    handle_anomaly(anomaly, fleet_parser, issue_creator)

            # ── 3. Check for stuck devices ──
            stuck = fleet_parser.check_all_stuck(timeout=120)
            for a in stuck:
                handle_anomaly(a, fleet_parser, issue_creator)

            # ── 4. Fleet health (every 5 minutes) ──
            now = time.time()
            if now - last_health_report > 300:
                report = generate_health_report(devices, fleet_parser)
                log_health(report)

                screens = report.get("screen_distribution", {})
                ig = screens.get("instagram", 0)
                home = screens.get("home_screen", 0)
                settings = screens.get("settings", 0)
                other = screens.get("other", 0) + screens.get("unknown", 0) + screens.get("error", 0)

                log.info(
                    f"[Health] {len(devices)} devices | "
                    f"IG={ig} Home={home} Settings={settings} Other={other} | "
                    f"Anomalies this period: {len(anomalies_this_cycle)}"
                )
                last_health_report = now

            # ── 5. Pattern report (every hour) ──
            if now - last_pattern_report > 3600:
                summary = fleet_parser.get_summary()
                if summary.get("recent_by_category"):
                    log.info(f"[Pattern Report] {json.dumps(summary['recent_by_category'])}")
                last_pattern_report = now

        except Exception as e:
            log.error(f"[Cycle {cycle}] Error: {e}")

        if single_pass:
            log.info("Single pass complete.")
            break

        # Sleep until next cycle
        elapsed = time.time() - cycle_start
        sleep_time = max(0, interval - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)


def main():
    parser = argparse.ArgumentParser(description="QA Monitor Daemon")
    parser.add_argument("--interval", "-i", type=int, default=30, help="Check interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run single pass then exit")
    args = parser.parse_args()
    run_monitor(interval=args.interval, single_pass=args.once)


if __name__ == "__main__":
    main()
