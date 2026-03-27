"""QA Agent configuration."""
import os
import sys
from pathlib import Path

QA_DIR = Path(__file__).parent.resolve()
MASTER_AGENT_DIR = QA_DIR.parent / "master-agent"

# Import master-agent settings for ADB path
if MASTER_AGENT_DIR.is_dir():
    sys.path.insert(0, str(MASTER_AGENT_DIR))
    from config.settings import ADB_PATH, IS_WINDOWS, ANTHROPIC_API_KEY
else:
    # Fallback
    ADB_PATH = os.environ.get("ADB_PATH", "adb")
    IS_WINDOWS = os.name == "nt"
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Vision API
VISION_MODEL = "claude-sonnet-4-20250514"
VISION_MAX_TOKENS = 1024

# Screenshot settings
SCREENSHOT_MAX_WIDTH = 800  # Resize for API efficiency
SCREENSHOT_DIR = QA_DIR / "screenshots"
REPORTS_DIR = QA_DIR / "reports"

# Concurrency
BATCH_SIZE = 5  # How many phones to process in parallel
