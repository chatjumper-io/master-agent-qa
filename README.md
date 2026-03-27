# master-agent-qa

QA testing agent for the Instagram automation bot (master-agent).

**This is a standalone QA tool. It does NOT modify master-agent code.**

## Setup

1. Clone this repo alongside `master-agent` on Server35:
   ```
   cd "C:\Users\mtlof\OneDrive\Desktop\ADB BOT"
   git clone https://github.com/chatjumper-io/master-agent-qa.git
   ```

2. Run tests:
   ```
   cd master-agent-qa
   py qa_scan.py
   ```

## What it does

- Connects to all phones via ADB
- Checks device health (battery, screen, connectivity)
- Identifies what screen each phone is on
- Tests UI dump functionality
- Tests Brain API connectivity
- Outputs a JSON report to `qa_report.json`

## Architecture

```
master-agent-qa/          <-- This repo (read-only QA)
master-agent/             <-- The bot (untouched)
```

The QA agent reads master-agent's code via imports but never writes to it.
