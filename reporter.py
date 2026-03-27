"""Generate reports and HTML dashboard."""
import json
import base64
from pathlib import Path
from datetime import datetime


def generate_html_dashboard(report, screenshots_dir, output_path):
    """Create a self-contained HTML dashboard with embedded screenshots."""
    devices = report.get("devices", [])
    summary = report.get("summary", {})
    ts = report.get("timestamp", "unknown")

    # Build device cards
    cards_html = ""
    for d in devices:
        serial = d.get("serial", "?")
        status = d.get("status", "unknown")
        vision = d.get("vision", {})
        summary_text = vision.get("summary", "No analysis")
        screen_type = vision.get("screen_type", "unknown")
        confidence = vision.get("confidence", 0)
        bugs = vision.get("bugs", [])
        action = vision.get("recommended_action", "none")
        heal = d.get("heal_result", {})

        # Status color
        colors = {
            "OK": "#22c55e", "MINOR": "#84cc16", "WARN": "#eab308",
            "BROKEN": "#f97316", "CRITICAL": "#ef4444", "unknown": "#6b7280"
        }
        color = colors.get(status, "#6b7280")

        # Screenshot thumbnail (base64 embedded)
        ss_path = Path(screenshots_dir) / f"{serial}.png"
        img_tag = '<div class="no-img">No screenshot</div>'
        if ss_path.exists():
            try:
                b64 = base64.b64encode(ss_path.read_bytes()).decode()
                img_tag = f'<img src="data:image/png;base64,{b64}" alt="{serial}" loading="lazy">'
            except Exception:
                pass

        # Bug list
        bugs_html = ""
        for bug in bugs:
            sev = bug.get("severity", "?")
            sev_colors = {"critical": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#84cc16"}
            bc = sev_colors.get(sev, "#6b7280")
            bugs_html += f'<div class="bug"><span class="sev" style="color:{bc}">[{sev.upper()}]</span> {bug.get("type","")}: {bug.get("description","")}</div>'

        if not bugs_html:
            bugs_html = '<div class="bug ok">No bugs detected</div>'

        # Heal result
        heal_html = ""
        if heal.get("actions_taken"):
            heal_html = f'<div class="heal">Actions: {", ".join(heal["actions_taken"])}</div>'

        cards_html += f"""
        <div class="card" style="border-color: {color}">
            <div class="card-header">
                <span class="serial">{serial}</span>
                <span class="status" style="background:{color}">{status}</span>
            </div>
            <div class="card-body">
                <div class="screenshot">{img_tag}</div>
                <div class="info">
                    <div class="summary">{summary_text}</div>
                    <div class="meta">Screen: {screen_type} | Confidence: {confidence:.0%} | Action: {action}</div>
                    <div class="bugs">{bugs_html}</div>
                    {heal_html}
                </div>
            </div>
        </div>"""

    # Summary counts
    s = report.get("summary_counts", {})

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>QA Dashboard - {ts}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }}
h1 {{ font-size: 24px; margin-bottom: 8px; color: #f8fafc; }}
.meta-header {{ color: #94a3b8; margin-bottom: 20px; font-size: 14px; }}
.summary-bar {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
.summary-item {{ background: #1e293b; padding: 12px 20px; border-radius: 8px; text-align: center; min-width: 100px; }}
.summary-item .count {{ font-size: 28px; font-weight: bold; }}
.summary-item .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(500px, 1fr)); gap: 16px; }}
.card {{ background: #1e293b; border-radius: 12px; border-left: 4px solid; overflow: hidden; }}
.card-header {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; background: #0f172a40; }}
.serial {{ font-family: monospace; font-size: 13px; }}
.status {{ padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; color: #000; }}
.card-body {{ display: flex; gap: 12px; padding: 12px; }}
.screenshot {{ flex-shrink: 0; width: 180px; }}
.screenshot img {{ width: 100%; border-radius: 8px; cursor: pointer; transition: transform 0.2s; }}
.screenshot img:hover {{ transform: scale(2.5); position: relative; z-index: 10; }}
.no-img {{ width: 180px; height: 320px; background: #374151; border-radius: 8px; display: flex; align-items: center; justify-content: center; color: #6b7280; }}
.info {{ flex: 1; display: flex; flex-direction: column; gap: 6px; }}
.summary {{ font-size: 14px; font-weight: 500; }}
.meta {{ font-size: 11px; color: #64748b; }}
.bugs {{ font-size: 12px; }}
.bug {{ margin: 2px 0; }}
.bug.ok {{ color: #22c55e; }}
.sev {{ font-weight: bold; font-size: 11px; }}
.heal {{ font-size: 11px; color: #38bdf8; margin-top: 4px; }}
</style></head><body>
<h1>QA Dashboard</h1>
<div class="meta-header">Scan: {ts} | Devices: {report.get('device_count', '?')}</div>
<div class="summary-bar">
    <div class="summary-item"><div class="count" style="color:#22c55e">{s.get('OK',0)}</div><div class="label">OK</div></div>
    <div class="summary-item"><div class="count" style="color:#eab308">{s.get('WARN',0)}</div><div class="label">Warn</div></div>
    <div class="summary-item"><div class="count" style="color:#f97316">{s.get('BROKEN',0)}</div><div class="label">Broken</div></div>
    <div class="summary-item"><div class="count" style="color:#ef4444">{s.get('CRITICAL',0)}</div><div class="label">Critical</div></div>
    <div class="summary-item"><div class="count">{report.get('device_count',0)}</div><div class="label">Total</div></div>
</div>
<div class="grid">{cards_html}</div>
</body></html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    return str(output_path)
