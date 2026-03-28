"""
Log parser — reads master-agent bot logs and extracts anomalies.
Supports both live file tailing and parsing log strings from GUI output.
"""
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Severity(Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Anomaly:
    timestamp: str
    serial: str
    severity: Severity
    category: str  # stuck_screen, crash, task_failed, offline, brain_mismatch, ui_dump_fail, etc.
    message: str
    raw_log: str = ""
    context: dict = field(default_factory=dict)


# Patterns that indicate problems
ANOMALY_PATTERNS = [
    # UI dump failures
    (r"Failed to extract UI hierarchy after \d+ attempts",
     Severity.ERROR, "ui_dump_fail", "UI hierarchy dump failed — phone likely playing video"),

    # Brain idle
    (r"No actionable task from Brain \(reason=(\w+)\)",
     Severity.WARN, "brain_idle", "Brain returned no task: {0}"),

    # IMEI failures
    (r"IMEI unavailable\. Waiting (\d+)s",
     Severity.ERROR, "imei_unavailable", "IMEI resolution failed — phone will idle {0}s"),

    # Navigation failures
    (r"go_home ✗|go_home failed|go_explore ✗",
     Severity.ERROR, "nav_failure", "Navigation failed — bot can't reach target tab"),

    (r"All back-retries exhausted.*Restarting app",
     Severity.ERROR, "nav_exhausted", "All navigation retries exhausted — force restarting app"),

    (r"BACK-fallback.*BACK closed the app",
     Severity.WARN, "nav_app_closed", "BACK press accidentally closed the app"),

    # Task failures
    (r"Task (\d+) -> failed",
     Severity.ERROR, "task_failed", "Brain task {0} reported as failed"),

    (r"Action not mapped to Layer 3 yet: (\w+)",
     Severity.ERROR, "action_not_implemented", "Action {0} has no handler — feature not implemented"),

    # App crashes
    (r"Crash clone|Close app|app has stopped|keeps stopping",
     Severity.CRITICAL, "app_crash", "App crash detected"),

    (r"Fatal error: (.+)",
     Severity.CRITICAL, "fatal_error", "Fatal error in bot loop: {0}"),

    # Network issues
    (r"IP did not change|IP rotation.*FAILED",
     Severity.WARN, "ip_rotation_failed", "IP rotation failed — same IP after toggle"),

    (r"Post-task IP rotation error",
     Severity.WARN, "ip_rotation_error", "IP rotation threw an exception"),

    # Login/ban issues
    (r"is banned|action.?blocked|challenge.?required|temporarily locked",
     Severity.CRITICAL, "account_banned", "Account ban or block detected"),

    (r"not logged in|login.?required|is not logged in",
     Severity.ERROR, "logged_out", "Account is logged out"),

    # Clone count issues
    (r"Count Clones:.*is banned",
     Severity.WARN, "clone_banned", "Clone found banned during count"),

    (r"Count Clones:.*not logged in",
     Severity.WARN, "clone_logged_out", "Clone found logged out during count"),

    # Connection issues
    (r"Connection error|Could not connect|device not found|offline",
     Severity.CRITICAL, "device_offline", "Device connection lost"),

    (r"adb server.*daemon.*restart|daemon started successfully",
     Severity.WARN, "adb_restart", "ADB daemon restarted — may disconnect devices"),

    # Popup issues
    (r"Popup guardian closed (\d+) popup",
     Severity.INFO, "popup_dismissed", "Dismissed {0} popup(s)"),
]


class LogParser:
    """Parses bot log lines and emits anomalies."""

    def __init__(self, serial: str = "unknown"):
        self.serial = serial
        self._compiled = [(re.compile(p, re.IGNORECASE), sev, cat, msg) for p, sev, cat, msg in ANOMALY_PATTERNS]
        # Track state for stuck detection
        self._last_activity_time = time.time()
        self._last_screen = ""
        self._screen_stuck_since = 0.0

    def parse_line(self, line: str) -> Optional[Anomaly]:
        """Parse a single log line. Returns Anomaly if one is detected, else None."""
        line = line.strip()
        if not line:
            return None

        # Extract timestamp if present [HH:MM:SS]
        ts_match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]', line)
        ts = ts_match.group(1) if ts_match else time.strftime("%H:%M:%S")

        for pattern, severity, category, msg_template in self._compiled:
            match = pattern.search(line)
            if match:
                # Format message with captured groups
                try:
                    groups = match.groups()
                    msg = msg_template.format(*groups) if groups else msg_template
                except (IndexError, KeyError):
                    msg = msg_template

                return Anomaly(
                    timestamp=ts,
                    serial=self.serial,
                    severity=severity,
                    category=category,
                    message=msg,
                    raw_log=line,
                )

        # Track activity for stuck detection
        self._last_activity_time = time.time()
        return None

    def check_stuck(self, timeout_seconds: float = 60.0) -> Optional[Anomaly]:
        """Check if the phone has been idle too long (no log activity)."""
        idle_time = time.time() - self._last_activity_time
        if idle_time > timeout_seconds:
            return Anomaly(
                timestamp=time.strftime("%H:%M:%S"),
                serial=self.serial,
                severity=Severity.WARN,
                category="phone_stuck",
                message=f"No log activity for {idle_time:.0f}s — phone may be stuck",
            )
        return None


class FleetLogParser:
    """Manages log parsers for multiple devices."""

    def __init__(self):
        self.parsers: dict[str, LogParser] = {}
        self.anomaly_history: list[Anomaly] = []
        self._max_history = 1000

    def get_parser(self, serial: str) -> LogParser:
        if serial not in self.parsers:
            self.parsers[serial] = LogParser(serial=serial)
        return self.parsers[serial]

    def ingest_line(self, serial: str, line: str) -> Optional[Anomaly]:
        """Parse a log line for a specific device."""
        parser = self.get_parser(serial)
        anomaly = parser.parse_line(line)
        if anomaly:
            self.anomaly_history.append(anomaly)
            if len(self.anomaly_history) > self._max_history:
                self.anomaly_history = self.anomaly_history[-self._max_history:]
        return anomaly

    def check_all_stuck(self, timeout: float = 60.0) -> list[Anomaly]:
        """Check all tracked devices for stuck state."""
        stuck = []
        for serial, parser in self.parsers.items():
            a = parser.check_stuck(timeout)
            if a:
                stuck.append(a)
        return stuck

    def get_anomalies_since(self, seconds_ago: float) -> list[Anomaly]:
        """Get anomalies from the last N seconds."""
        cutoff = time.time() - seconds_ago
        return [a for a in self.anomaly_history
                if time.strptime(a.timestamp, "%H:%M:%S") is not None]  # simplified

    def get_summary(self) -> dict:
        """Fleet health summary."""
        total = len(self.parsers)
        categories = {}
        for a in self.anomaly_history[-200:]:
            categories[a.category] = categories.get(a.category, 0) + 1

        return {
            "devices_tracked": total,
            "total_anomalies": len(self.anomaly_history),
            "recent_by_category": categories,
        }
