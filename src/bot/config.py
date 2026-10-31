import json
import logging
import os
import re
import shutil
import socket
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

import time

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

SETTINGS_FILE = BASE_DIR / "data" / "user_settings.json"


def get_bin_dir() -> Path:
    return (BASE_DIR / "bin").resolve()


# Ensure bin folder is in PATH for all child processes and libraries
_bin_str = str(get_bin_dir())
if _bin_str not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _bin_str + os.pathsep + os.environ.get("PATH", "")


def load_user_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_user_setting(key: str, value: Any) -> None:
    try:
        data = load_user_settings()
        data[key] = value
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not save user setting {key}: {e}")


def is_proxy_alive(proxy_url: Optional[str], timeout: float = 0.06) -> bool:
    """Checks whether the specified proxy host:port is actively listening."""
    if not proxy_url:
        return False
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if not port:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def resolve_binary(name: str) -> str:
    """Prioritizes local bundled binary in bin/ before system PATH."""
    bundled = get_bin_dir() / f"{name}.exe"
    if bundled.exists():
        return str(bundled.resolve())
    found = shutil.which(name)
    if found:
        return found
    return name


def resolve_ffmpeg_dir() -> str:
    """Returns directory containing ffmpeg and ffprobe for yt-dlp."""
    bin_folder = get_bin_dir()
    if (bin_folder / "ffmpeg.exe").exists():
        return str(bin_folder)
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent.resolve())
    return str(bin_folder)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    bot_token: Optional[str] = Field(default="", description="Telegram Bot API Token")
    proxy_url: Optional[str] = Field("auto", description="Primary proxy URL ('auto', 'direct', or custom)")
    proxy_fallback_url: Optional[str] = Field(None, description="Secondary fallback proxy URL")
    data_dir: Path = Field(default=BASE_DIR / "data")
    download_dir: Path = Field(default=BASE_DIR / "data" / "downloads")
    transcript_dir: Path = Field(default=BASE_DIR / "data" / "transcripts")
    max_file_size_mb: int = Field(default=48, description="Target size threshold in MB for compression")
    ffmpeg_dir: str = Field(default_factory=resolve_ffmpeg_dir, description="Directory containing FFmpeg & FFprobe")
    ffmpeg_path: str = Field(default_factory=lambda: resolve_binary("ffmpeg"), description="Path to FFmpeg")
    ffprobe_path: str = Field(default_factory=lambda: resolve_binary("ffprobe"), description="Path to FFprobe")
    node_path: Optional[str] = Field(default_factory=lambda: shutil.which("node") or r"C:\Program Files\nodejs\node.exe", description="Path to Node.js")

    _cached_proxy: Optional[str] = None
    _proxy_cache_time: float = 0.0
    _cache_ttl: float = 20.0

    def __init__(self, **values):
        super().__init__(**values)
        user_data = load_user_settings()
        if "download_dir" in user_data:
            try:
                custom_p = Path(user_data["download_dir"]).resolve()
                custom_p.mkdir(parents=True, exist_ok=True)
                self.download_dir = custom_p
            except Exception as e:
                logger.warning(f"Failed to restore persisted download_dir: {e}")

    def ensure_directories(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

    def clear_proxy_cache(self) -> None:
        """Invalidates the proxy detection cache."""
        self._cached_proxy = None
        self._proxy_cache_time = 0.0

    def set_download_dir(self, new_dir: str) -> Path:
        """Sets and persists the custom download directory."""
        resolved = Path(new_dir).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        self.download_dir = resolved
        save_user_setting("download_dir", str(resolved))

        # Safe update of .env file (without regex escape corruption)
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            try:
                lines = env_file.read_text(encoding="utf-8").splitlines()
                new_lines = []
                found = False
                for line in lines:
                    if line.strip().startswith("DOWNLOAD_DIR="):
                        new_lines.append(f"DOWNLOAD_DIR={resolved.as_posix()}")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"DOWNLOAD_DIR={resolved.as_posix()}")
                env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            except Exception as e:
                logger.warning(f"Could not persist DOWNLOAD_DIR in .env: {e}")
        return resolved

    def get_active_proxy(self, force_refresh: bool = False) -> Optional[str]:
        """
        Intelligently resolves an active, working proxy with fast TTL caching:
        - If cached within TTL -> returns instantly (0ms)
        - If proxy_url == 'direct' -> None (direct connection)
        - If explicit proxy URL provided and reachable -> returns it
        - If 'auto' or explicit proxy unreachable -> fast-probes unique ports
        - If none reachable -> None (falls back to direct connection)
        """
        now = time.time()
        if not force_refresh and (now - self._proxy_cache_time < self._cache_ttl):
            return self._cached_proxy

        raw = (self.proxy_url or "").strip().lower()

        if raw == "direct":
            self._cached_proxy = None
            self._proxy_cache_time = now
            return None

        # If user specified a concrete proxy (not 'auto')
        if raw and raw != "auto":
            if is_proxy_alive(self.proxy_url):
                self._cached_proxy = self.proxy_url
                self._proxy_cache_time = now
                return self.proxy_url
            logger.warning(f"Configured proxy {self.proxy_url} is NOT reachable. Falling back to auto-probe...")

        if self.proxy_fallback_url and is_proxy_alive(self.proxy_fallback_url):
            self._cached_proxy = self.proxy_fallback_url
            self._proxy_cache_time = now
            return self.proxy_fallback_url

        # Auto-probe common local proxy ports for V2Ray / Clash / NekoRay / Hiddify / Sing-box
        candidates = [
            "http://127.0.0.1:10808",
            "socks5://127.0.0.1:10808",
            "http://127.0.0.1:10809",
            "socks5://127.0.0.1:10809",
            "http://127.0.0.1:12334",
            "socks5://127.0.0.1:12334",
            "http://127.0.0.1:2334",
            "http://127.0.0.1:7890",
            "socks5://127.0.0.1:7890",
            "http://127.0.0.1:2081",
            "socks5://127.0.0.1:2080",
        ]

        tested_ports = set()
        resolved_proxy = None
        for candidate in candidates:
            parsed = urlparse(candidate)
            port = parsed.port
            if port in tested_ports:
                continue
            if is_proxy_alive(candidate):
                logger.info(f"Auto-detected active proxy: {candidate}")
                resolved_proxy = candidate
                break
            tested_ports.add(port)

        self._cached_proxy = resolved_proxy
        self._proxy_cache_time = now
        return resolved_proxy


settings = Settings()
