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
    Parses LRC format timestamps: [mm:ss.xx] or [mm:ss] or [hh:mm:ss]
    Returns list of {"time": float, "text": str} sorted chronologically.
    """
    lines: List[Dict[str, Any]] = []
    # Regex matches [01:23.45], [01:23], [1:15:30.50], [01:15:30]
    pattern = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\](.*)")

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            hours = int(m.group(1)) if m.group(1) is not None else 0
            mins = int(m.group(2))
            secs = int(m.group(3))
            millis_str = m.group(4) or "0"
            if len(millis_str) == 2:
                millis = int(millis_str) * 10
            elif len(millis_str) == 1:
                millis = int(millis_str) * 100
            else:
                millis = int(millis_str[:3])
            timestamp = hours * 3600 + mins * 60 + secs + (millis / 1000.0)
            text = m.group(5).strip()
            lines.append({"time": round(timestamp, 2), "text": text})

    lines.sort(key=lambda x: x["time"])
    return lines


def parse_subtitles(content: str) -> List[Dict[str, Any]]:
    """
    Parses WebVTT and SRT subtitle contents into timestamped line entries:
    [{"time": float_seconds, "text": "cleaned text line"}]
    """
    pattern = re.compile(
        r'(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{2,3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{2,3})'
    )
    lines = content.splitlines()
    entries: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = pattern.search(line)
        if m:
            h = int(m.group(1) or 0)
            m_val = int(m.group(2))
            s = int(m.group(3))
            ms = int(m.group(4).ljust(3, '0')[:3])
            start_sec = round(h * 3600 + m_val * 60 + s + ms / 1000.0, 2)

            text_lines = []
            i += 1
            while i < len(lines):
                t_line = lines[i].strip()
                if not t_line or pattern.search(t_line):
                    break
                clean = re.sub(r'<[^>]+>', '', t_line).strip()
                if clean and not clean.isdigit() and not clean.startswith("WEBVTT"):
                    text_lines.append(clean)
                i += 1
            if text_lines:
                entries.append({'time': start_sec, 'text': ' '.join(text_lines)})
            continue
        i += 1

    entries.sort(key=lambda x: x["time"])
    return entries


def normalize_track_metadata(raw_title: str = "", raw_artist: str = "", filename: str = "", artist: str = "") -> Tuple[str, str]:
    """
    Cleans noisy titles, filenames, and artists to extract genuine (title, artist).
    Examples:
      - "OneRepublic_-_Counting_Stars" -> ("Counting Stars", "OneRepublic")
      - "Rick Astley - Never Gonna Give You Up [dQw4w9WgXcQ]" -> ("Never Gonna Give You Up", "Rick Astley")
      - "Alan Walker - Faded (Official Music Video)" -> ("Faded", "Alan Walker")
    """
    # 1. Determine candidate string
    candidate = (raw_title or "").strip()
    if not candidate and filename:
        candidate = Path(filename).name

    # Strip common audio/video extension
    candidate = re.sub(r'(?i)\.(mp3|m4a|mp4|webm|opus|ogg|flac|wav|mkv|aac)$', '', candidate).strip()

    # Remove 11-char YouTube ID like [dQw4w9WgXcQ] or (dQw4w9WgXcQ)
    candidate = re.sub(r'\[[a-zA-Z0-9_\-]{11}\]', '', candidate)
    candidate = re.sub(r'\([a-zA-Z0-9_\-]{11}\)', '', candidate)

    # Strip extension again if was at the end
    candidate = re.sub(r'(?i)\.(mp3|m4a|mp4|webm|opus|ogg|flac|wav|mkv|aac)$', '', candidate).strip()

    # Replace underscore separators: "_-_" -> " - ", then remaining "_" -> " "
    candidate = re.sub(r'[\s_]*-[\s_]*', ' - ', candidate)
    candidate = re.sub(r'_+', ' ', candidate)

    # Remove boilerplate video/audio keywords
    noise_pattern = re.compile(
        r'(?i)[\(\[\{]\s*((official\s*)?(music\s*)?(video|audio)|lyrics?|audio|remix|live|4k|hd|hq|video|clip|mv|visualizer|feat\.?.*|ft\.?.*)\s*[\)\]\}]'
    )
    candidate = noise_pattern.sub('', candidate)
    candidate = re.sub(r'\s+', ' ', candidate).strip()

    # Clean artist
    artist = (artist or raw_artist or "").strip()
    generic_artists = [
        "پخش زنده درون‌برنامه‌ای", "پخش زنده", "یوتیوب", "اسپاتیفای", 
        "youtube & spotify downloader", "youtube", "spotify", "unknown artist", "unknown"
    ]
    if artist.lower() in [g.lower() for g in generic_artists] or artist == raw_title:
        artist = ""

    title = candidate

    # If artist is still empty, try splitting "Artist - Title"
    if not artist and " - " in candidate:
        parts = candidate.split(" - ", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            artist = parts[0].strip()
            title = parts[1].strip()

    # Final cleanup of quotes and noise
    title = re.sub(r'^["\'\s]+|["\'\s]+$', '', title)
    artist = re.sub(r'^["\'\s]+|["\'\s]+$', '', artist)

    return (title or candidate, artist)


class LyricsService:
    @staticmethod
    def get_cache_path(title: str, artist: str) -> Path:
        key = f"{artist.lower().strip()}_{title.lower().strip()}"
        cache_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return LYRICS_CACHE_DIR / f"{cache_hash}.json"

    @staticmethod
    def _pair_fa_lines(lines: List[Dict[str, Any]], fa_lines: List[Dict[str, Any]]):
        """Attaches fa_text to each line in lines by matching timestamps or index."""
        if not lines or not fa_lines:
            return
        if len(lines) == len(fa_lines):
            for i in range(len(lines)):
                lines[i]["fa_text"] = fa_lines[i].get("text", "")
            return

        for line in lines:
            t = line.get("time", 0.0)
            closest_text = ""
            min_diff = 2.0
            for fa in fa_lines:
                diff = abs(fa.get("time", 0.0) - t)
                if diff < min_diff:
                    min_diff = diff
                    closest_text = fa.get("text", "")
            line["fa_text"] = closest_text

    @classmethod
    async def get_lyrics(
        cls,
        media_path: Optional[Path] = None,
        title: str = "",
        artist: str = "",
        album: str = "",
        duration: float = 0.0,
        include_translation: bool = True,
    ) -> Dict[str, Any]:
        """
        Retrieves synced or plain lyrics for a track:
        1. Checks local .lrc and _persian.lrc companion files next to media_path
        2. Normalizes title & artist metadata
        3. Checks persistent cache in data/.cache/lyrics/
        4. Multi-tier LRCLIB API querying
        5. Automatically loads or translates Persian synced lyrics
        """
        # 1. Local .lrc, .srt, .vtt file check
        if media_path and media_path.exists() and media_path.is_file():
            candidates = [
                media_path.with_suffix(".lrc"),
                media_path.with_suffix(".srt"),
                media_path.with_suffix(".vtt"),
                media_path.with_name(f"{media_path.stem}.en.srt"),
                media_path.with_name(f"{media_path.stem}.en.vtt"),
            ]
            primary_file = next((f for f in candidates if f.exists() and f.stat().st_size > 0), None)

            fa_candidates = [
                media_path.with_name(f"{media_path.stem}_persian.lrc"),
                media_path.with_name(f"{media_path.stem}_fa.lrc"),
                media_path.with_name(f"{media_path.stem}_persian.srt"),
                media_path.with_name(f"{media_path.stem}.fa.srt"),
                media_path.with_name(f"{media_path.stem}_persian.vtt"),
                media_path.with_name(f"{media_path.stem}.fa.vtt"),
            ]
            companion_fa = next((f for f in fa_candidates if f.exists() and f.stat().st_size > 0), None)

            if primary_file or companion_fa:
                try:
                    lines: List[Dict[str, Any]] = []
                    content = ""
                    if primary_file:
                        content = primary_file.read_text(encoding="utf-8", errors="ignore")
                        lines = parse_lrc(content) if primary_file.suffix == ".lrc" else parse_subtitles(content)

                    fa_lines: List[Dict[str, Any]] = []
                    fa_synced = ""
                    fa_plain = ""
                    bilingual = ""
                    if companion_fa:
                        fa_synced = companion_fa.read_text(encoding="utf-8", errors="ignore")
                        fa_lines = parse_lrc(fa_synced) if companion_fa.suffix == ".lrc" else parse_subtitles(fa_synced)
                        fa_plain = "\n".join(l["text"] for l in fa_lines if l.get("text"))

                    if lines and fa_lines:
                        cls._pair_fa_lines(lines, fa_lines)
                        bilingual = "\n".join(
                            f"{l.get('text', '')}\n{l.get('fa_text', '')}"
                            for l in lines
                            if l.get("text") or l.get("fa_text")
                        )
                    elif not lines and fa_lines:
                        lines = fa_lines

                    if lines and include_translation and not fa_lines:
                        try:
                            from src.services.spotify import spotify_service
                            trans = await spotify_service.translate_lyrics_to_persian(content, "")
                            fa_synced = trans.get("persian_synced", "")
                            fa_plain = trans.get("persian_plain", "")
                            bilingual = trans.get("bilingual", "")
                            if fa_synced:
                                fa_lines = parse_lrc(fa_synced)
                                cls._pair_fa_lines(lines, fa_lines)
                                try:
                                    save_fa = media_path.with_name(f"{media_path.stem}_persian.lrc")
                                    save_fa.write_text(fa_synced, encoding="utf-8")
                                except Exception:
                                    pass
                        except Exception as te:
                            logger.debug(f"Local subtitle auto-translation error: {te}")

                    if lines:
                        return {
                            "success": True,
                            "has_lyrics": True,
                            "is_synced": True,
                            "source": "local",
                            "clean_title": title or media_path.stem,
                            "clean_artist": artist or "YouTube",
                            "lines": lines,
                            "plain_lyrics": "\n".join(l["text"] for l in lines if l.get("text")),
                            "synced_lyrics": content,
                            "persian_lines": fa_lines,
                            "persian_synced": fa_synced,
                            "persian_plain": fa_plain,
                            "bilingual": bilingual,
                        }
                except Exception as e:
                    logger.debug(f"Failed to read local subtitle/lyrics: {e}")

        # 1.5. On-demand YouTube Video transcript resolution
        fname = media_path.name if media_path else ""
        v_id_match = re.search(r'[\[\(]([a-zA-Z0-9_\-]{11})[\]\)]', fname) or re.search(r'[\[\(]([a-zA-Z0-9_\-]{11})[\]\)]', title)
        video_id = v_id_match.group(1) if v_id_match else None
        if not video_id and fname:
            m_pre = re.match(r'^([a-zA-Z0-9_\-]{11})(_|\.|$)', fname)
            if m_pre:
                video_id = m_pre.group(1)

        if video_id:
            try:
                from src.services.subtitles import SubtitleService
                sub_serv = SubtitleService()
                yt_url = f"https://www.youtube.com/watch?v={video_id}"
                en_text, _ = await asyncio.to_thread(sub_serv._sync_fetch_transcript, yt_url, "en", video_id)
                fa_text, _ = await asyncio.to_thread(sub_serv._sync_fetch_transcript, yt_url, "fa", video_id)

                if en_text or fa_text:
                    parsed_en = parse_lrc(en_text) if en_text else []
                    parsed_fa = parse_lrc(fa_text) if fa_text else []

                    if media_path and media_path.exists():
                        try:
                            if en_text:
                                media_path.with_suffix(".lrc").write_text(en_text, encoding="utf-8")
                            if fa_text:
                                media_path.with_name(f"{media_path.stem}_persian.lrc").write_text(fa_text, encoding="utf-8")
                        except Exception:
                            pass

                    target_lines = parsed_en if parsed_en else parsed_fa
                    if parsed_en and parsed_fa:
                        cls._pair_fa_lines(target_lines, parsed_fa)

                    return {
                        "success": True,
                        "has_lyrics": True,
                        "is_synced": True,
                        "source": "youtube_transcript",
                        "clean_title": title or fname,
                        "clean_artist": artist or "YouTube",
                        "lines": target_lines,
                        "plain_lyrics": "\n".join(l["text"] for l in target_lines if l.get("text")),
                        "synced_lyrics": en_text,
                        "persian_lines": parsed_fa,
                        "persian_synced": fa_text,
                        "persian_plain": "\n".join(l["text"] for l in parsed_fa if l.get("text")),
                        "bilingual": "",
                    }
            except Exception as y_err:
                logger.debug(f"YouTube on-demand transcript fetch failed: {y_err}")

        # 2. Normalize metadata
        clean_title, clean_artist = normalize_track_metadata(title, artist, fname)

        if not clean_title:
            return {
                "success": False,
                "has_lyrics": False,
                "is_synced": False,
                "source": "none",
                "clean_title": "",
                "clean_artist": "",
                "lines": [],
                "plain_lyrics": "",
                "synced_lyrics": "",
                "persian_lines": [],
                "persian_synced": "",
                "persian_plain": "",
                "bilingual": "",
            }

        # 3. Disk Cache check
        cache_file = cls.get_cache_path(clean_title, clean_artist)
        if cache_file.exists() and cache_file.stat().st_size > 0:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("has_lyrics"):
                    has_fa = bool(cached.get("persian_synced") or (cached.get("lines") and cached["lines"][0].get("fa_text")))
                    if include_translation and not has_fa and (cached.get("synced_lyrics") or cached.get("plain_lyrics")):
                        # Upgrade old cache on-the-fly with Persian translation
                        try:
                            from src.services.spotify import spotify_service
                            trans = await spotify_service.translate_lyrics_to_persian(
                                cached.get("synced_lyrics", ""), cached.get("plain_lyrics", "")
                            )
                            cached["persian_synced"] = trans.get("persian_synced", "")
                            cached["persian_plain"] = trans.get("persian_plain", "")
                            cached["bilingual"] = trans.get("bilingual", "")
                            if cached.get("persian_synced"):
                                fa_lines = parse_lrc(cached["persian_synced"])
                                cached["persian_lines"] = fa_lines
                                if cached.get("lines"):
                                    cls._pair_fa_lines(cached["lines"], fa_lines)
                            cache_file.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
                        except Exception as te:
                            logger.debug(f"Cache upgrade translation failed: {te}")

                    cached["clean_title"] = clean_title
                    cached["clean_artist"] = clean_artist
                    return cached
            except Exception:
                pass

        # 4. Multi-tier Query LRCLIB
        proxy_url = settings.get_active_proxy()
        synced_lrc = ""
        plain_lrc = ""

        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=8.0, follow_redirects=True) as client:
                # Tier 1: Exact GET
                params: Dict[str, str] = {"track_name": clean_title}
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

                # Tier 2: Search with Artist + Title
                if not synced_lrc and not plain_lrc:
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

                # Tier 3: Search with Title only
                if not synced_lrc and not plain_lrc and clean_artist:
                    resp3 = await client.get("https://lrclib.net/api/search", params={"q": clean_title})
                    if resp3.status_code == 200:
                        items = resp3.json()
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

        # 5. Persian Translation if available / requested
        fa_lines: List[Dict[str, Any]] = []
        persian_synced = ""
        persian_plain = ""
        bilingual = ""

        if include_translation and (synced_lrc or plain_lrc):
            try:
                from src.services.spotify import spotify_service
                trans = await spotify_service.translate_lyrics_to_persian(synced_lrc, plain_lrc)
                persian_synced = trans.get("persian_synced", "")
                persian_plain = trans.get("persian_plain", "")
                bilingual = trans.get("bilingual", "")
                if persian_synced:
                    fa_lines = parse_lrc(persian_synced)
                    cls._pair_fa_lines(parsed_lines, fa_lines)
            except Exception as e:
                logger.debug(f"Translation step skipped or failed: {e}")

        result = {
            "success": bool(synced_lrc or plain_lrc),
            "has_lyrics": bool(synced_lrc or plain_lrc),
            "is_synced": is_synced,
            "source": "lrclib",
            "clean_title": clean_title,
            "clean_artist": clean_artist,
            "lines": parsed_lines,
            "plain_lyrics": plain_lrc or ("\n".join(l["text"] for l in parsed_lines if l["text"])),
            "synced_lyrics": synced_lrc,
            "persian_lines": fa_lines,
            "persian_synced": persian_synced,
            "persian_plain": persian_plain,
            "bilingual": bilingual,
        }

        # Cache result only if lyrics were found
        if result["has_lyrics"]:
            try:
                cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        return result


lyrics_service = LyricsService()
