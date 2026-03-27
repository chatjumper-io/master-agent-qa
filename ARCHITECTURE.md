# QA Agent Architecture

## The Problem
We have 40 phones running Instagram automation. We need an autonomous agent that can:
1. **See** what each phone is actually showing (not just metadata)
2. **Understand** if what it's showing is correct or a bug
3. **Act** to fix simple issues (dismiss popups, relaunch apps, press back)
4. **Report** with visual evidence and actionable bug descriptions

## Key Insight
We don't need scrcpy or remote desktop viewers. `adb exec-out screencap -p` captures the exact same pixels. The missing piece is **vision analysis** — sending those screenshots to an AI that can understand what's on screen and classify bugs.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   QA Agent (Python)                  │
│              Runs on Server35 alongside bot          │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐│
│  │ ADB Layer│──>│Screenshot│──>│ Vision Analysis   ││
│  │          │   │ Capture  │   │ (Claude/OpenAI)   ││
│  │-screencap│   │ -PNG     │   │ -What screen?     ││
│  │-dumpsys  │   │ -resize  │   │ -Any popups?      ││
│  │-ui dump  │   │ -batch   │   │ -Stuck/broken?    ││
│  │-shell    │   │          │   │ -What should it do││
│  └──────────┘   └──────────┘   └────────┬─────────┘│
│                                          │          │
│  ┌──────────────────────────────────────┐│          │
│  │         Bug Classifier               ││          │
│  │                                      ││          │
│  │ Vision result + ADB metadata =       ││          │
│  │ structured bug report per device     │◄──────────┘
│  │                                      │           │
│  │ Categories:                          │           │
│  │ - STUCK: wrong screen, frozen        │           │
│  │ - BLOCKED: action block popup        │           │
│  │ - LOGGED_OUT: login screen           │           │
│  │ - CRASHED: app not responding        │           │
│  │ - CHALLENGE: verification needed     │           │
│  │ - IDLE: on home, not doing anything  │           │
│  │ - WORKING: actively automating       │           │
│  └──────────────┬───────────────────────┘           │
│                 │                                    │
│  ┌──────────────▼───────────────────────┐           │
│  │         Auto-Healer (optional)       │           │
│  │                                      │           │
│  │ Simple fixes the agent can do:       │           │
│  │ - Press BACK to dismiss popups       │           │
│  │ - Relaunch Instagram                 │           │
│  │ - Wake screen if off                 │           │
│  │ - Dismiss "Not Now" / "OK" dialogs   │           │
│  │ - Go HOME then reopen app            │           │
│  │                                      │           │
│  │ Complex fixes → flag for human:      │           │
│  │ - Account banned/challenged          │           │
│  │ - Phone needs physical attention     │           │
│  │ - Unknown screen state               │           │
│  └──────────────┬───────────────────────┘           │
│                 │                                    │
│  ┌──────────────▼───────────────────────┐           │
│  │         Reporter                     │           │
│  │                                      │           │
│  │ Outputs:                             │           │
│  │ - JSON report with all device data   │           │
│  │ - HTML dashboard with screenshots    │           │
│  │ - Git push to QA repo                │           │
│  │ - Webhook/API notification           │           │
│  │                                      │           │
│  │ Screenshots stored as:               │           │
│  │ - Thumbnails in git (resized 400px)  │           │
│  │ - Full-res locally on server         │           │
│  └──────────────────────────────────────┘           │
└─────────────────────────────────────────────────────┘
```

## Vision Analysis Prompt Strategy

For each phone screenshot, send to Claude Vision with:

```
You are a QA inspector for an Instagram automation bot farm.
This is a screenshot from phone {serial}.

Analyze what you see and respond with JSON:
{
  "screen_type": "instagram_feed|instagram_dm|instagram_profile|instagram_login|
                  instagram_challenge|instagram_blocked|instagram_error|
                  phone_home|phone_settings|app_crash|popup_dialog|
                  other_app|black_screen|unknown",
  "is_working": true/false,
  "bugs": [
    {"type": "...", "severity": "critical|high|medium|low", "description": "..."}
  ],
  "visible_text": ["key text elements visible on screen"],
  "instagram_state": {
    "logged_in": true/false/unknown,
    "current_tab": "home|search|reels|profile|dm|none",
    "popup_visible": true/false,
    "popup_type": "action_block|challenge|update|permission|crash|none",
    "feed_scrolling": true/false
  },
  "recommended_action": "none|press_back|dismiss_popup|relaunch_app|
                         wake_screen|needs_human|relogin_required",
  "confidence": 0.0-1.0,
  "summary": "One sentence description of what this phone is doing"
}
```

## Screenshot Pipeline

1. **Capture**: `adb -s {serial} exec-out screencap -p > screenshot.png`
2. **Resize**: Shrink to 800px wide (saves API tokens, still readable)
3. **Batch**: Process 5 phones at a time (API rate limits)
4. **Analyze**: Send to Claude Vision API
5. **Classify**: Parse JSON response into structured bug data
6. **Act**: Execute recommended_action if auto-heal enabled
7. **Report**: Compile all results

## File Structure

```
master-agent-qa/
├── qa_agent.py          # Main orchestrator
├── capture.py           # ADB screenshot capture
├── vision.py            # Claude/OpenAI vision analysis
├── classifier.py        # Bug classification logic
├── healer.py            # Auto-fix simple issues
├── reporter.py          # Generate reports + HTML dashboard
├── config.py            # API keys, paths, settings
├── screenshots/         # Organized by timestamp
│   └── 20260327_170000/
│       ├── R3CN207M6SN.png
│       ├── R3CN207M6SN_thumb.png
│       └── ...
├── reports/             # JSON + HTML reports
│   └── 20260327_170000/
│       ├── report.json
│       └── dashboard.html
└── README.md
```

## Run Modes

```bash
# Full scan — screenshot + analyze all phones
py qa_agent.py scan

# Quick check — just screenshot, no vision analysis
py qa_agent.py capture

# Single device deep inspection
py qa_agent.py inspect R3CN207M6SN

# Auto-heal mode — fix what it can, report the rest
py qa_agent.py heal

# Continuous monitoring — run every N minutes
py qa_agent.py watch --interval 15

# Generate HTML dashboard from last scan
py qa_agent.py dashboard
```

## API Requirements

- **Anthropic API key** (Claude Vision) — already in master-agent config
  OR
- **OpenAI API key** (GPT-4 Vision)

The bot already has `ANTHROPIC_API_KEY` in its environment for Claude-powered DM conversations, so we can reuse that.

## HTML Dashboard

Self-contained HTML file with:
- Grid of phone screenshots (thumbnails)
- Click to expand full-size
- Color-coded borders (green=OK, yellow=warn, red=critical)
- Bug list per device
- Summary stats
- Auto-refreshes if served via HTTP

This can be opened locally on the server or pushed to GitHub Pages.

## Advantages Over Current Approach

1. **No GetScreen needed** — runs directly on the server
2. **No scrcpy needed** — ADB screencap is identical
3. **AI-powered analysis** — catches bugs humans miss
4. **Autonomous** — can run on a schedule, fix issues, alert on problems
5. **Visual evidence** — every bug comes with a screenshot
6. **Scalable** — same code works for 10 or 100 phones
