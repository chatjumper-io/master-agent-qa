"""Vision analysis using Claude API."""
import base64
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from config import ANTHROPIC_API_KEY, VISION_MODEL, VISION_MAX_TOKENS

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SYSTEM_PROMPT = """You are a QA inspector for an Instagram automation bot farm running on Samsung Galaxy S20 phones. Each phone runs Instagram (main + clone packages) controlled by an ADB-based automation bot.

Your job: analyze a phone screenshot and identify exactly what state the phone is in, whether it's working correctly, and what bugs exist.

ALWAYS respond with valid JSON only. No markdown, no explanation outside JSON."""

USER_PROMPT_TEMPLATE = """This is a screenshot from phone {serial}.
ADB metadata: {metadata}

Analyze what you see and respond with this exact JSON structure:
{{
  "screen_type": "instagram_feed|instagram_dm|instagram_profile|instagram_login|instagram_reels|instagram_search|instagram_challenge|instagram_blocked|instagram_error|instagram_story|phone_home|phone_settings|phone_lockscreen|app_crash_dialog|popup_dialog|automagic|other_app|black_screen|unknown",
  "is_working": true or false,
  "bugs": [
    {{"type": "string", "severity": "critical|high|medium|low", "description": "what is wrong"}}
  ],
  "instagram_state": {{
    "logged_in": true or false or null,
    "current_tab": "home|search|reels|profile|dm|none|unknown",
    "popup_visible": true or false,
    "popup_type": "action_block|challenge|update|permission|crash|login_required|notification|cookie|none",
    "content_visible": "description of what content is shown"
  }},
  "recommended_action": "none|press_back|dismiss_popup|relaunch_app|wake_screen|go_home_and_relaunch|needs_human|relogin_required",
  "confidence": 0.0 to 1.0,
  "summary": "One clear sentence: what is this phone doing right now?"
}}"""


def analyze_screenshot(serial, screenshot_path, metadata=None):
    """Send screenshot to Claude Vision and get structured analysis."""
    if not ANTHROPIC_API_KEY:
        return {"error": "No ANTHROPIC_API_KEY configured", "serial": serial}

    img_path = Path(screenshot_path)
    if not img_path.exists():
        return {"error": f"Screenshot not found: {screenshot_path}", "serial": serial}

    # Read and base64 encode
    img_data = img_path.read_bytes()
    b64 = base64.standard_b64encode(img_data).decode("utf-8")

    # Determine media type
    suffix = img_path.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"

    # Build metadata string
    meta_str = json.dumps(metadata or {}, indent=None, default=str)

    # Build request
    body = {
        "model": VISION_MODEL,
        "max_tokens": VISION_MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT_TEMPLATE.format(
                            serial=serial, metadata=meta_str
                        ),
                    },
                ],
            }
        ],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Extract text content
        text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        # Parse JSON from response
        # Handle potential markdown code blocks
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        analysis = json.loads(text)
        analysis["serial"] = serial
        return analysis

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return {
            "error": f"API error {e.code}: {err_body[:200]}",
            "serial": serial,
        }
    except json.JSONDecodeError as e:
        return {
            "error": f"Invalid JSON from vision API: {e}",
            "raw_response": text[:500] if text else "",
            "serial": serial,
        }
    except Exception as e:
        return {"error": str(e), "serial": serial}
