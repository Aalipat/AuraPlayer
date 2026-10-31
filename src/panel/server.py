"""
Server runner for YouTube & Spotify Downloader Desktop Panel.
Safely handles headless/GUI mode (pythonw.exe) where sys.stdout and sys.stderr are None.
"""
import os
import sys
from pathlib import Path

# Determine project base directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Set working directory to project root
os.chdir(str(BASE_DIR))

# Ensure bin folder is in PATH for ffmpeg and ffprobe
bin_dir = str(BASE_DIR / "bin")
current_path = os.environ.get("PATH", "")
if bin_dir not in current_path:
    os.environ["PATH"] = bin_dir + os.pathsep + current_path

# Ensure data directory exists
data_dir = BASE_DIR / "data"
data_dir.mkdir(parents=True, exist_ok=True)

# Guard against None stdout/stderr in pythonw.exe
log_file_path = data_dir / "server.log"
try:
    if sys.stdout is None or sys.stderr is None:
        log_file = open(log_file_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_file
        if sys.stderr is None:
            sys.stderr = log_file
except Exception:
    pass

import logging
import uvicorn
from src.panel.app import app

# Setup safe uvicorn logging config that never calls sys.stdout.isatty()
SAFE_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "safe_formatter": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "safe_handler": {
            "formatter": "safe_formatter",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["safe_handler"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["safe_handler"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["safe_handler"], "level": "INFO", "propagate": False},
    },
}

def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    
    print(f"[*] Starting YouTube & Spotify Downloader Server on http://{host}:{port}")
    uvicorn.run(
        "src.panel.app:app",
        host=host,
        port=port,
        log_config=SAFE_LOG_CONFIG,
        access_log=False,
        timeout_keep_alive=30,
    )

if __name__ == "__main__":
    main()
