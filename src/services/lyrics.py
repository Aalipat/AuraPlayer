"""
Lyrics Service with Local LRC parsing, LRCLIB API querying, and caching.
Supports synchronized timestamps [mm:ss.xx] for live karaoke display.
"""
import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.bot.config import settings

logger = logging.getLogger(__name__)

LYRICS_CACHE_DIR = settings.data_dir / ".cache" / "lyrics"
LYRICS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def parse_lrc(lrc_text: str) -> List[Dict[str, Any]]:
    """
    Parses LRC format timestamps: [mm:ss.xx] or [mm:ss.xxx] or [mm:ss]
    Returns list of {"time": float, "text": str} sorted chronologically.
    """
    lines: List[Dict[str, Any]] = []
    # Regex matches [01:23.45] or [01:23] or [01:23.456]
    pattern = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\](.*)")

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            mins = int(m.group(1))
            secs = int(m.group(2))
            millis_str = m.group(3) or "0"
            if len(millis_str) == 2:
                millis = int(millis_str) * 10
            elif len(millis_str) == 1:
                millis = int(millis_str) * 100
            else:
                millis = int(millis_str[:3])
            timestamp = mins * 60 + secs + (millis / 1000.0)
            text = m.group(4).strip()
            lines.append({"time": round(timestamp, 2), "text": text})

    lines.sort(key=lambda x: x["time"])
    return lines


class LyricsService:
    @staticmethod
    def get_cache_path(title: str, artist: str) -> Path:
        key = f"{artist.lower().strip()}_{title.lower().strip()}"
        cache_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return LYRICS_CACHE_DIR / f"{cache_hash}.json"

    @classmethod
    async def get_lyrics(
        cls,
        media_path: Optional[Path] = None,
        title: str = "",
        artist: str = "",
        album: str = "",
        duration: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Retrieves synced or plain lyrics for a track:
        1. Checks local .lrc companion file next to media_path
        2. Checks persistent cache in data/.cache/lyrics/
        3. Queries LRCLIB public API (with proxy fallback)
        """
        # 1. Local .lrc file check
        if media_path and media_path.exists() and media_path.is_file():
            companion_lrc = media_path.with_suffix(".lrc")
            if companion_lrc.exists() and companion_lrc.stat().st_size > 0:
                try:
                    content = companion_lrc.read_text(encoding="utf-8", errors="ignore")
                    lines = parse_lrc(content)
                    if lines:
                        return {
                            "success": True,
                            "has_lyrics": True,
                            "is_synced": True,
                            "source": "local",
                            "lines": lines,
                            "plain_lyrics": "\n".join(l["text"] for l in lines if l["text"]),
                        }
                except Exception as e:
                    logger.debug(f"Failed to read local .lrc: {e}")

        clean_title = title.strip()
        clean_artist = artist.strip()

        if not clean_title:
            return {
                "success": False,
                "has_lyrics": False,
                "is_synced": False,
                "source": "none",
                "lines": [],
                "plain_lyrics": "",
            }

        # 2. Disk Cache check
        cache_file = cls.get_cache_path(clean_title, clean_artist)
        if cache_file.exists() and cache_file.stat().st_size > 0:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("has_lyrics"):
                    return cached
            except Exception:
                pass

        # 3. Query LRCLIB
        proxy_url = settings.get_active_proxy()
        synced_lrc = ""
        plain_lrc = ""

        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=8.0, follow_redirects=True) as client:
                params = {"track_name": clean_title}
                if clean_artist:
                    params["artist_name"] = clean_artist
                if album:
                    params["album_name"] = album
                if duration and duration > 0:
                    params["duration"] = str(int(duration))

                resp = await client.get("https://lrclib.net/api/get", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    synced_lrc = data.get("syncedLyrics") or ""
                    plain_lrc = data.get("plainLyrics") or ""

                if not synced_lrc and not plain_lrc:
                    # Fallback to search endpoint
                    q = f"{clean_artist} {clean_title}".strip()
                    resp2 = await client.get("https://lrclib.net/api/search", params={"q": q})
                    if resp2.status_code == 200:
                        items = resp2.json()
                        if isinstance(items, list) and items:
                            for it in items:
                                if it.get("syncedLyrics"):
                                    synced_lrc = it.get("syncedLyrics")
                                    plain_lrc = it.get("plainLyrics") or ""
                                    break
                            if not synced_lrc and items[0].get("plainLyrics"):
                                plain_lrc = items[0].get("plainLyrics")

        except Exception as err:
            logger.warning(f"LRCLIB request failed: {err}")

        parsed_lines = parse_lrc(synced_lrc) if synced_lrc else []
        is_synced = len(parsed_lines) > 0

        result = {
            "success": bool(synced_lrc or plain_lrc),
            "has_lyrics": bool(synced_lrc or plain_lrc),
            "is_synced": is_synced,
            "source": "lrclib",
            "lines": parsed_lines,
            "plain_lyrics": plain_lrc or ("\n".join(l["text"] for l in parsed_lines if l["text"])),
        }

        # Cache result
        try:
            cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        return result


lyrics_service = LyricsService()
