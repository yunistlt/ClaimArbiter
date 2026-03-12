"""
Entry point for the admin web server.

Usage (local):
    cd /path/to/ClaimArbiter
    source .venv/bin/activate
    cd src
    python -m web.run

Usage (Docker / docker-compose):
    command: python -m web.run
    working_dir: /app/src
"""

import os
import sys

# Ensure src/ is on the path when run as __main__
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(_SRC_DIR) == "web":
    _SRC_DIR = os.path.dirname(_SRC_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Load .env from project root before starting
from dotenv import load_dotenv  # noqa: E402

_PROJECT_ROOT = os.path.join(_SRC_DIR, "..")
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "8080"))
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
