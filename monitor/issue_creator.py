"""
GitHub issue creator — files bugs automatically when anomalies are detected.
Deduplicates issues to avoid flooding.
"""
import json
import time
import hashlib
import urllib.request
import urllib.error
import ssl
from typing import Optional
from pathlib import Path

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# Issue dedup window — don't create same issue within this many seconds
DEDUP_WINDOW = 3600  # 1 hour


class GitHubIssueCreator:
    """Creates GitHub issues for detected anomalies."""

    def __init__(self, token: str, repo: str = "chatjumper-io/master-agent"):
        self.token = token
        self.repo = repo
        self.api_base = f"https://api.github.com/repos/{repo}"
        self._created_hashes: dict[str, float] = {}  # hash -> last created timestamp

    def _request(self, path: str, method: str = "GET", data: dict = None) -> dict:
        url = f"{self.api_base}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            return {"error": f"HTTP {e.code}", "detail": body[:300]}
        except Exception as e:
            return {"error": str(e)}

    def _dedup_hash(self, category: str, serial: str, message: str) -> str:
        """Create a hash to deduplicate similar issues."""
        key = f"{category}:{serial}:{message[:100]}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _is_duplicate(self, dedup_hash: str) -> bool:
        last = self._created_hashes.get(dedup_hash, 0)
        return (time.time() - last) < DEDUP_WINDOW

    def create_issue(self, title: str, body: str, labels: list = None,
                     category: str = "", serial: str = "") -> dict:
        """Create a GitHub issue if not a duplicate."""
        # Dedup check
        h = self._dedup_hash(category, serial, title)
        if self._is_duplicate(h):
            return {"skipped": True, "reason": "duplicate", "hash": h}

        data = {
            "title": title,
            "body": body,
            "labels": labels or ["bot-qa", "auto-detected"],
        }

        result = self._request("/issues", method="POST", data=data)

        if "number" in result:
            self._created_hashes[h] = time.time()
            return {"created": True, "number": result["number"], "url": result.get("html_url", "")}

        return {"created": False, "error": result.get("error", "unknown")}

    def create_anomaly_issue(self, anomaly, snapshot=None) -> dict:
        """Create an issue from an Anomaly + optional PhoneSnapshot."""
        severity_labels = {
            "critical": ["bug", "P0: critical", "bot-qa"],
            "error": ["bug", "P1: high", "bot-qa"],
            "warn": ["needs-triage", "bot-qa"],
            "info": ["bot-qa"],
        }

        sev = anomaly.severity.value if hasattr(anomaly.severity, 'value') else str(anomaly.severity)
        labels = severity_labels.get(sev, ["bot-qa"])

        title = f"[QA Bot] {anomaly.category}: {anomaly.message[:80]}"

        body_parts = [
            f"## Auto-detected by QA Monitor",
            f"",
            f"**Device**: `{anomaly.serial}`",
            f"**Severity**: {sev.upper()}",
            f"**Category**: `{anomaly.category}`",
            f"**Time**: {anomaly.timestamp}",
            f"",
            f"### Description",
            f"{anomaly.message}",
            f"",
            f"### Raw Log",
            f"```",
            f"{anomaly.raw_log[:500]}",
            f"```",
        ]

        if snapshot:
            body_parts += [
                f"",
                f"### Phone State at Time of Anomaly",
                f"- **Screen**: `{snapshot.screen_type}`",
                f"- **Top Activity**: `{snapshot.top_activity[:100]}`",
                f"- **Current Focus**: `{snapshot.current_focus[:100]}`",
                f"- **IG Packages**: {', '.join(snapshot.ig_packages) or 'none'}",
                f"- **Airplane Mode**: {snapshot.airplane_mode}",
            ]

            if snapshot.ui_texts:
                body_parts += [
                    f"",
                    f"### Visible UI Text",
                    f"```",
                    f"{chr(10).join(snapshot.ui_texts[:20])}",
                    f"```",
                ]

            if snapshot.log_tail:
                body_parts += [
                    f"",
                    f"### Last 20 Log Lines",
                    f"```",
                    *[f"{l}" for l in snapshot.log_tail[-20:]],
                    f"```",
                ]

        body = "\n".join(body_parts)

        return self.create_issue(
            title=title, body=body, labels=labels,
            category=anomaly.category, serial=anomaly.serial,
        )
