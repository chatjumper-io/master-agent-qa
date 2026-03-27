"""
QA Agent - Autonomous Instagram bot farm inspector.

Captures phone screenshots via ADB, analyzes them with Claude Vision,
classifies bugs, optionally auto-heals, and generates visual reports.

Usage:
  py qa_agent.py scan              # Full scan: screenshot + vision + report
  py qa_agent.py scan --heal       # Full scan + auto-fix simple issues
  py qa_agent.py scan --limit 5    # Scan first 5 devices only
  py qa_agent.py capture           # Screenshots only (no vision)
  py qa_agent.py inspect SERIAL    # Deep inspect one device
  py qa_agent.py dashboard         # Regenerate dashboard from last scan
"""
import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from collections import Counter

from config import SCREENSHOT_DIR, REPORTS_DIR, ANTHROPIC_API_KEY
from capture import get_devices, take_screenshot, collect_device_data
from vision import analyze_screenshot
from healer import heal_device
from reporter import generate_html_dashboard


def scan(devices, scan_dir, do_vision=True, do_heal=False):
    """Run full QA scan on given devices."""
    ss_dir = scan_dir / "screenshots"
    ss_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_id": scan_dir.name,
        "device_count": len(devices),
        "vision_enabled": do_vision,
        "heal_enabled": do_heal,
        "devices": [],
        "summary_counts": {},
    }

    for i, serial in enumerate(devices):
        print(f"\n[{i+1}/{len(devices)}] {serial}")
        device = {"serial": serial, "index": i + 1}

        # 1. Screenshot
        ss_path = ss_dir / f"{serial}.png"
        print(f"  Capturing screenshot...", end=" ", flush=True)
        ss_ok, ss_size = take_screenshot(serial, ss_path)
        if ss_ok:
            print(f"OK ({ss_size // 1024}KB)")
        else:
            print("FAILED")
        device["screenshot"] = {"ok": ss_ok, "path": str(ss_path), "size": ss_size}

        # 2. ADB metadata
        print(f"  Collecting ADB data...", end=" ", flush=True)
        metadata = collect_device_data(serial)
        device["metadata"] = metadata
        print("OK")

        # 3. Vision analysis
        if do_vision and ss_ok and ANTHROPIC_API_KEY:
            print(f"  Analyzing with Claude Vision...", end=" ", flush=True)
            analysis = analyze_screenshot(serial, ss_path, metadata)
            device["vision"] = analysis
            if "error" in analysis:
                print(f"ERROR: {analysis['error'][:80]}")
            else:
                print(f"{analysis.get('screen_type', '?')} | {analysis.get('summary', '?')[:60]}")
        elif do_vision and not ANTHROPIC_API_KEY:
            print("  Vision SKIPPED (no API key)")
            device["vision"] = {"error": "no_api_key"}
        elif not ss_ok:
            device["vision"] = {"error": "no_screenshot"}

        # 4. Classify status
        vision = device.get("vision", {})
        bugs = vision.get("bugs", [])
        severities = [b.get("severity", "") for b in bugs]
        if "error" in vision:
            status = "unknown"
        elif "critical" in severities:
            status = "CRITICAL"
        elif "high" in severities:
            status = "BROKEN"
        elif "medium" in severities:
            status = "WARN"
        elif not vision.get("is_working", True):
            status = "BROKEN"
        elif bugs:
            status = "MINOR"
        else:
            status = "OK"
        device["status"] = status

        # 5. Auto-heal
        if do_heal and status in ("BROKEN", "WARN", "CRITICAL"):
            print(f"  Healing...", end=" ", flush=True)
            heal_result = heal_device(serial, vision)
            device["heal_result"] = heal_result
            if heal_result.get("success"):
                print(f"OK: {', '.join(heal_result.get('actions_taken', []))}")
            else:
                print(f"FAILED: {', '.join(heal_result.get('actions_taken', []))}")

        report["devices"].append(device)

        # Rate limit for vision API
        if do_vision and ANTHROPIC_API_KEY and i < len(devices) - 1:
            time.sleep(1)

    # Summary
    statuses = [d["status"] for d in report["devices"]]
    report["summary_counts"] = dict(Counter(statuses))

    return report


def cmd_scan(args):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_dir = REPORTS_DIR / ts
    scan_dir.mkdir(parents=True, exist_ok=True)

    devices = get_devices()
    if args.limit:
        devices = devices[:args.limit]
    if not devices:
        print("No devices found!")
        return

    print(f"{'='*60}")
    print(f"  QA AGENT - Full Scan")
    print(f"  Devices: {len(devices)} | Vision: {bool(ANTHROPIC_API_KEY)} | Heal: {args.heal}")
    print(f"  Output: {scan_dir}")
    print(f"{'='*60}")

    report = scan(devices, scan_dir, do_vision=True, do_heal=args.heal)

    # Save JSON
    json_path = scan_dir / "report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nJSON report: {json_path}")

    # Generate dashboard
    dash_path = scan_dir / "dashboard.html"
    generate_html_dashboard(report, scan_dir / "screenshots", dash_path)
    print(f"HTML dashboard: {dash_path}")

    # Print summary
    s = report["summary_counts"]
    print(f"\n{'='*60}")
    print(f"RESULTS: " + " | ".join(f"{k}={v}" for k, v in sorted(s.items())))
    print(f"{'='*60}")

    # Print per-device summaries
    for d in report["devices"]:
        v = d.get("vision", {})
        print(f"  [{d['status']:8s}] {d['serial']} - {v.get('summary', v.get('error', 'no analysis'))}")


def cmd_capture(args):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ss_dir = SCREENSHOT_DIR / ts
    ss_dir.mkdir(parents=True, exist_ok=True)

    devices = get_devices()
    if args.limit:
        devices = devices[:args.limit]

    print(f"Capturing {len(devices)} devices to {ss_dir}")
    for i, serial in enumerate(devices):
        path = ss_dir / f"{serial}.png"
        ok, size = take_screenshot(serial, path)
        tag = f"OK ({size//1024}KB)" if ok else "FAILED"
        print(f"  [{i+1}/{len(devices)}] {serial}: {tag}")

    print(f"\nScreenshots saved: {ss_dir}")


def cmd_inspect(args):
    serial = args.serial
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_dir = REPORTS_DIR / f"inspect_{serial}_{ts}"
    scan_dir.mkdir(parents=True, exist_ok=True)

    print(f"Deep inspection of {serial}")
    report = scan([serial], scan_dir, do_vision=True, do_heal=False)

    json_path = scan_dir / "report.json"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # Print full analysis
    d = report["devices"][0]
    v = d.get("vision", {})
    print(f"\n{'='*60}")
    print(f"Device: {serial}")
    print(f"Status: {d['status']}")
    print(f"Screen: {v.get('screen_type', '?')}")
    print(f"Working: {v.get('is_working', '?')}")
    print(f"Summary: {v.get('summary', '?')}")
    if v.get("instagram_state"):
        ig = v["instagram_state"]
        print(f"IG logged in: {ig.get('logged_in')} | Tab: {ig.get('current_tab')} | Popup: {ig.get('popup_type')}")
    if v.get("bugs"):
        print(f"\nBugs:")
        for bug in v["bugs"]:
            print(f"  [{bug['severity'].upper()}] {bug['type']}: {bug['description']}")
    print(f"\nRecommended action: {v.get('recommended_action', 'none')}")
    print(f"Confidence: {v.get('confidence', 0):.0%}")
    print(f"{'='*60}")


def cmd_dashboard(args):
    # Find most recent report
    if not REPORTS_DIR.exists():
        print("No reports found")
        return
    dirs = sorted(REPORTS_DIR.iterdir(), reverse=True)
    for d in dirs:
        rp = d / "report.json"
        if rp.exists():
            report = json.loads(rp.read_text())
            dash = d / "dashboard.html"
            generate_html_dashboard(report, d / "screenshots", dash)
            print(f"Dashboard generated: {dash}")
            return
    print("No reports found")


def main():
    parser = argparse.ArgumentParser(description="QA Agent for Instagram bot farm")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Full scan with vision analysis")
    p_scan.add_argument("--heal", action="store_true", help="Auto-fix simple issues")
    p_scan.add_argument("--limit", "-l", type=int, help="Max devices")

    p_cap = sub.add_parser("capture", help="Screenshot only (no vision)")
    p_cap.add_argument("--limit", "-l", type=int, help="Max devices")

    p_inspect = sub.add_parser("inspect", help="Deep inspect single device")
    p_inspect.add_argument("serial", help="Device serial number")

    sub.add_parser("dashboard", help="Regenerate HTML dashboard")

    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "capture":
        cmd_capture(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
