"""
Spotify service for metadata extraction, lyrics retrieval (LRCLIB),
AI line-by-line Persian translation, and audio search matching.
Works without Spotify Premium or API credentials.
"""
import asyncio
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yt_dlp

from src.bot.config import settings
from src.utils.formatting import format_seconds

logger = logging.getLogger(__name__)

# In-memory TTL cache for Spotify metadata (5 minutes)
_SPOTIFY_META_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_SPOTIFY_CACHE_TTL: float = 300.0

SPOTIFY_URL_REGEX = re.compile(
    r'(?:https?:\/\/)?(?:open\.)?spotify\.com\/(?:intl-[a-z]{2}\/)?(?:user\/[^\/]+\/)?(track|album|playlist)\/([a-zA-Z0-9]+)',
    re.IGNORECASE
)
SPOTIFY_URI_REGEX = re.compile(
    r'spotify:(track|album|playlist):([a-zA-Z0-9]+)',
    re.IGNORECASE
)
SPOTIFY_LINK_REGEX = re.compile(
    r'https?:\/\/spotify\.link\/[a-zA-Z0-9]+',
    re.IGNORECASE
)


def parse_spotify_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Extracts (entity_type, entity_id) from Spotify URL or URI."""
    url = url.strip()
    match = SPOTIFY_URL_REGEX.search(url)
    if match:
        return match.group(1).lower(), match.group(2)
    match_uri = SPOTIFY_URI_REGEX.search(url)
    if match_uri:
        return match_uri.group(1).lower(), match_uri.group(2)
    return None, None


def is_valid_spotify_url(url: str) -> bool:
    """Checks whether the URL is a supported Spotify link."""
    if "spotify.link/" in url.lower():
        return True
    entity_type, entity_id = parse_spotify_url(url)
    return entity_type is not None and entity_id is not None


class SpotifyService:
    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url

    def _get_proxy_url(self) -> Optional[str]:
        return self.proxy_url or settings.get_active_proxy()

    def _get_base_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'socket_timeout': 30,
            'retries': 10,
            'extractor_retries': 5,
            'ffmpeg_location': settings.ffmpeg_dir,
        }
        active = self._get_proxy_url()
        if active:
            opts['proxy'] = active
        if settings.node_path and Path(settings.node_path).exists():
            opts['js_runtimes'] = {'node': {'path': str(settings.node_path)}}
        return opts

    async def get_metadata(self, url: str) -> Dict[str, Any]:
        """
        Extracts official Spotify metadata for track, album, or playlist
        by querying Spotify's server-rendered embed data (__NEXT_DATA__) with TTL cache.
        """
        clean_url = url.strip()
        now = time.time()
        if clean_url in _SPOTIFY_META_CACHE:
            ts, cached_data = _SPOTIFY_META_CACHE[clean_url]
            if now - ts < _SPOTIFY_CACHE_TTL:
                return cached_data

        url = clean_url
        proxy_url = self._get_proxy_url()

        # Handle shortened spotify.link URLs
        if "spotify.link" in url:
            try:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=10.0, follow_redirects=True) as client:
                    head_res = await client.head(url)
                    url = str(head_res.url)
            except Exception as e:
                logger.warning(f"Could not resolve spotify.link redirect: {e}")

        entity_type, entity_id = parse_spotify_url(url)
        if not entity_type or not entity_id:
            raise ValueError("لینک معتبر اسپاتیفای نیست.")

        embed_url = f"https://open.spotify.com/embed/{entity_type}/{entity_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        async with httpx.AsyncClient(proxy=proxy_url, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(embed_url, headers=headers)
            if resp.status_code != 200:
                if entity_type == "track":
                    res = await self._fallback_oembed_track(url, entity_id)
                    _SPOTIFY_META_CACHE[clean_url] = (now, res)
                    return res
                raise ValueError(f"پاسخی از اسپاتیفای دریافت نشد (کد: {resp.status_code})")
            html = resp.text

        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
        if not match:
            if entity_type == "track":
                res = await self._fallback_oembed_track(url, entity_id)
                _SPOTIFY_META_CACHE[clean_url] = (now, res)
                return res
            raise ValueError("عدم امکان خواندن متادیتا از صفحه اسپاتیفای.")

        try:
            data = json.loads(match.group(1))
            props = data.get('props') if isinstance(data, dict) else {}
            page_props = props.get('pageProps') if isinstance(props, dict) else {}

            if isinstance(page_props, dict) and page_props.get('status') == 404:
                raise ValueError("این پلی‌لیست یا آلبوم در اسپاتیفای پیدا نشد. لطفاً مطمئن شوید پلی‌لیست روی عمومی (Public) تنظیم است.")

            state = page_props.get('state') if isinstance(page_props, dict) else {}
            data_dict = state.get('data') if isinstance(state, dict) else {}
            entity = data_dict.get('entity') if isinstance(data_dict, dict) else {}

            if not entity and isinstance(page_props, dict):
                entity = page_props.get('entity') or page_props.get('playlist') or page_props.get('album') or {}
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"خطا در تجزیه متادیتای اسپاتیفای: {e}")

        if not entity:
            raise ValueError("اطلاعات پلی‌لیست یا آلبوم یافت نشد. ممکن است پلی‌لیست خصوصی (Private) باشد.")

        # Extract best cover (prefer 640x640)
        images = entity.get('visualIdentity', {}).get('image', []) if isinstance(entity.get('visualIdentity'), dict) else entity.get('images', [])
        cover_url = None
        if isinstance(images, list) and images:
            sorted_imgs = sorted(images, key=lambda x: x.get('maxWidth', 0) if isinstance(x, dict) else 0, reverse=True)
            if sorted_imgs and isinstance(sorted_imgs[0], dict):
                cover_url = sorted_imgs[0].get('url')
        elif isinstance(images, str):
            cover_url = images

        if entity_type == "track":
            title = entity.get('name') or entity.get('title') or "Unknown Title"
            artists_list = [a.get('name') for a in entity.get('artists', []) if isinstance(a, dict) and a.get('name')]
            artist = ", ".join(artists_list) if artists_list else entity.get('subtitle') or "Unknown Artist"

            album_obj = entity.get('album')
            album_name = album_obj.get('name') if isinstance(album_obj, dict) else (album_obj or title)

            release_date = entity.get('releaseDate', {})
            release_year = ""
            if isinstance(release_date, dict) and release_date.get('isoString'):
                release_year = release_date['isoString'][:4]
            elif isinstance(release_date, str):
                release_year = release_date[:4]

            duration_ms = entity.get('duration') or 0
            duration_sec = int(duration_ms / 1000)

            result = {
                "platform": "spotify",
                "entity_type": "track",
                "id": entity_id,
                "url": url,
                "title": title,
                "artist": artist,
                "album": album_name,
                "release_year": release_year,
                "cover": cover_url,
                "duration": format_seconds(duration_sec),
                "duration_seconds": duration_sec,
                "preview_url": entity.get('audioPreview', {}).get('url') if isinstance(entity.get('audioPreview'), dict) else None,
            }
            _SPOTIFY_META_CACHE[clean_url] = (now, result)
            return result

        elif entity_type in ("album", "playlist"):
            name = entity.get('name') or "Unknown Collection"
            owner = entity.get('subtitle') or ""
            track_items = entity.get('trackList') or []

            tracks = []
            for idx, t in enumerate(track_items, 1):
                if not isinstance(t, dict):
                    continue
                t_duration_ms = t.get('duration') or 0
                t_duration_sec = int(t_duration_ms / 1000)
                t_uri = t.get('uri') or ''
                t_id = t_uri.split(':')[-1] if ':' in t_uri else f"{entity_id}_{idx}"
                t_preview = t.get('audioPreview')
                preview_url = t_preview.get('url') if isinstance(t_preview, dict) else (t_preview if isinstance(t_preview, str) else None)
                tracks.append({
                    "track_num": idx,
                    "id": t_id,
                    "url": f"https://open.spotify.com/track/{t_id}",
                    "title": t.get('title') or f"Track {idx}",
                    "artist": t.get('subtitle') or owner or "Various Artists",
                    "duration": format_seconds(t_duration_sec),
                    "duration_seconds": t_duration_sec,
                    "preview_url": preview_url,
                })

            result = {
                "platform": "spotify",
                "entity_type": entity_type,
                "id": entity_id,
                "url": url,
                "title": name,
                "artist": owner,
                "cover": cover_url,
                "total_tracks": len(tracks),
                "tracks": tracks,
            }
            _SPOTIFY_META_CACHE[clean_url] = (now, result)
            return result

        raise ValueError(f"نوع لینک پشتیبانی نمی‌شود: {entity_type}")

    async def _fallback_oembed_track(self, url: str, track_id: str) -> Dict[str, Any]:
        """Fallback to Spotify oEmbed if embed page script is inaccessible."""
        oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
        proxy_url = self._get_proxy_url()
        async with httpx.AsyncClient(proxy=proxy_url, timeout=10.0) as client:
            resp = await client.get(oembed_url)
            data = resp.json()
            title = data.get('title', 'Unknown Track')
            cover = data.get('thumbnail_url')
            if cover and "00001e02" in cover:
                cover = cover.replace("00001e02", "0000b273")
            return {
                "platform": "spotify",
                "entity_type": "track",
                "id": track_id,
                "url": url,
                "title": title,
                "artist": "Spotify Artist",
                "album": title,
                "release_year": "",
                "cover": cover,
                "duration": "03:30",
                "duration_seconds": 210,
                "preview_url": None,
            }

    async def get_track_cover(self, track_id_or_url: str) -> Optional[str]:
        """
        Retrieves the exact individual 640x640 album artwork for a Spotify track.
        Works without Spotify credentials.
        First checks in-memory cache, then fast oEmbed (upgraded to 640x640),
        then full embed metadata.
        """
        if not track_id_or_url:
            return None

        t_id = str(track_id_or_url).strip()
        if "spotify.com" in t_id or "spotify:" in t_id:
            _, parsed_id = parse_spotify_url(t_id)
            if parsed_id:
                t_id = parsed_id

        track_url = f"https://open.spotify.com/track/{t_id}"

        # 1. Check cache
        if track_url in _SPOTIFY_META_CACHE:
            ts, cached_data = _SPOTIFY_META_CACHE[track_url]
            if time.time() - ts < _SPOTIFY_CACHE_TTL and cached_data.get("cover"):
                return cached_data["cover"]

        proxy_url = self._get_proxy_url()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }

        # 2. Fast lightweight Spotify oEmbed (~200ms)
        try:
            oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{t_id}"
            async with httpx.AsyncClient(proxy=proxy_url, timeout=6.0, follow_redirects=True) as client:
                res = await client.get(oembed_url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    thumb = data.get("thumbnail_url", "")
                    if thumb:
                        # Upgrade 300x300 (00001e02) to 640x640 HQ original cover (0000b273)
                        return thumb.replace("00001e02", "0000b273")
        except Exception as e:
            logger.debug(f"oEmbed cover fetch for track {t_id} error: {e}")

        # 3. Fallback to full get_metadata
        try:
            full = await self.get_metadata(track_url)
            if full.get("cover"):
                return full["cover"]
        except Exception as e:
            logger.debug(f"Full metadata cover fetch for track {t_id} error: {e}")

        return None

    async def get_lyrics(self, track_name: str, artist_name: str, duration: int = 0) -> Dict[str, Any]:
        """
        Fetches synced (.lrc) and plain lyrics from the free LRCLIB database using active proxy.
        """
        proxy_url = self._get_proxy_url()
        headers = {'User-Agent': 'YouTubeSpotifyDownloader/2.0'}
        synced_lrc, plain_lyrics = None, None

        async with httpx.AsyncClient(proxy=proxy_url, timeout=12.0, headers=headers) as client:
            # Strategy 1: exact query
            params = {"track_name": track_name, "artist_name": artist_name}
            if duration > 0:
                params["duration"] = str(duration)
            try:
                resp = await client.get("https://lrclib.net/api/get", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    synced_lrc = data.get('syncedLyrics')
                    plain_lyrics = data.get('plainLyrics')
            except Exception as e:
                logger.debug(f"LRCLIB direct get failed: {e}")

            # Strategy 2: search query
            if not (synced_lrc or plain_lyrics):
                try:
                    q = f"{artist_name} {track_name}"
                    resp2 = await client.get("https://lrclib.net/api/search", params={"q": q})
                    if resp2.status_code == 200:
                        items = resp2.json()
                        for item in items:
                            if item.get('syncedLyrics'):
                                synced_lrc = item.get('syncedLyrics')
                                plain_lyrics = item.get('plainLyrics')
                                break
                        if not synced_lrc and items and items[0].get('plainLyrics'):
                            plain_lyrics = items[0].get('plainLyrics')
                except Exception as e:
                    logger.warning(f"LRCLIB search failed: {e}")

        # Multi-provider fallback via syncedlyrics
        if not (synced_lrc or plain_lyrics):
            try:
                import syncedlyrics
                query = f"{artist_name} {track_name}".strip() or track_name
                fallback = await asyncio.to_thread(
                    syncedlyrics.search, query, providers=["NetEase", "Megalobiz", "Musixmatch"]
                )
                if fallback and fallback.strip():
                    synced_lrc = fallback.strip()
                    logger.info("Successfully fetched synced lyrics via fallback in spotify_service!")
            except Exception as se:
                logger.debug(f"syncedlyrics search failed in spotify_service: {se}")

        return {
            "has_lyrics": bool(synced_lrc or plain_lyrics),
            "is_synced": bool(synced_lrc),
            "synced_lyrics": synced_lrc or "",
            "plain_lyrics": plain_lyrics or (self._clean_lrc_to_plain(synced_lrc) if synced_lrc else ""),
        }

    def _clean_lrc_to_plain(self, lrc: str) -> str:
        """Removes timestamps from LRC to produce clean plain lyrics."""
        lines = []
        for line in lrc.splitlines():
            cleaned = re.sub(r'\[\d{2}:\d{2}(?:\.\d{2,3})?\]', '', line).strip()
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)

    async def translate_lyrics(self, synced_lrc: str, plain_lyrics: str = "", target_lang: str = "fa") -> Dict[str, str]:
        """
        Translates lyrics line-by-line into target_lang (Persian or English) while preserving exact LRC timestamps.
        Uses active proxy for Google Translate requests with multi-provider fallback.
        """
        source = synced_lrc if synced_lrc.strip() else plain_lyrics
        if not source.strip():
            return {"synced": "", "plain": "", "bilingual": "", "persian_synced": "", "persian_plain": ""}

        lines = source.splitlines()
        ts_regex = re.compile(r'^(\[\d{2}:\d{2}(?:\.\d{2,3})?\])\s*(.*)$')

        parsed_lines: List[Tuple[str, str]] = []
        text_chunks: List[str] = []

        for line in lines:
            m = ts_regex.match(line.strip())
            if m:
                ts, txt = m.group(1), m.group(2).strip()
                parsed_lines.append((ts, txt))
                if txt:
                    text_chunks.append(txt)
            else:
                stripped = line.strip()
                if stripped:
                    parsed_lines.append(("", stripped))
                    text_chunks.append(stripped)

        if not text_chunks:
            return {"synced": "", "plain": "", "bilingual": "", "persian_synced": "", "persian_plain": ""}

        proxy_url = self._get_proxy_url()

        def _translate_sync():
            import requests
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            translated_dict: Dict[str, str] = {}
            current_batch: List[str] = []
            current_len = 0

            def _flush_batch(batch: List[str]):
                if not batch:
                    return
                combined = "\n".join(batch)
                success = False

                # 1. Primary: Chrome extension translate endpoint (fast, unthrottled)
                try:
                    url = "https://clients5.google.com/translate_a/t"
                    params = {"client": "dict-chrome-ex", "sl": "auto", "tl": target_lang, "q": combined}
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    resp = requests.get(url, params=params, headers=headers, proxies=proxies, timeout=12)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_block = data[0][0] if isinstance(data[0], list) else data[0]
                        if isinstance(raw_block, str) and raw_block.strip():
                            translated_lines = raw_block.splitlines()
                            for original, trans in zip(batch, translated_lines):
                                translated_dict[original] = trans.strip()
                            success = True
                except Exception as e:
                    logger.debug(f"Clients5 translate failed: {e}")

                # 2. Fallback: Google GTX endpoint
                if not success:
                    try:
                        url = "https://translate.googleapis.com/translate_a/single"
                        params = {"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": combined}
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                        resp = requests.get(url, params=params, headers=headers, proxies=proxies, timeout=10)
                        if resp.status_code == 200:
                            data = resp.json()
                            translated_block = "".join([part[0] for part in data[0] if part and part[0]])
                            translated_lines = translated_block.splitlines()
                            for original, trans in zip(batch, translated_lines):
                                translated_dict[original] = trans.strip()
                            success = True
                    except Exception as e:
                        logger.debug(f"Google translate gtx error: {e}")

                # 3. Fallback: MyMemory Translation API
                if not success:
                    sl = "fa" if target_lang == "en" else "en"
                    for original in batch:
                        try:
                            mm_url = "https://api.mymemory.translated.net/get"
                            mm_params = {"q": original, "langpair": f"{sl}|{target_lang}"}
                            mm_resp = requests.get(mm_url, params=mm_params, proxies=proxies, timeout=8)
                            if mm_resp.status_code == 200:
                                res_json = mm_resp.json()
                                trans_txt = res_json.get("responseData", {}).get("translatedText")
                                if trans_txt and not trans_txt.startswith("MYMEMORY WARNING"):
                                    translated_dict[original] = trans_txt.strip()
                                    continue
                        except Exception:
                            pass
                        translated_dict[original] = original

            for txt in text_chunks:
                current_batch.append(txt)
                current_len += len(txt)
                if current_len >= 1500:
                    _flush_batch(current_batch)
                    current_batch = []
                    current_len = 0

            if current_batch:
                _flush_batch(current_batch)

            return translated_dict

        translated_dict = await asyncio.to_thread(_translate_sync)

        synced_lines = []
        plain_lines = []
        bilingual_lines = []

        for ts, orig_txt in parsed_lines:
            trans_txt = translated_dict.get(orig_txt, orig_txt)
            if ts:
                synced_lines.append(f"{ts} {trans_txt}")
                bilingual_lines.append(f"{ts} {orig_txt}")
                bilingual_lines.append(f"          ({trans_txt})")
            else:
                synced_lines.append(trans_txt)
                bilingual_lines.append(orig_txt)
                bilingual_lines.append(f"({trans_txt})")

            if trans_txt:
                plain_lines.append(trans_txt)

        result_synced = "\n".join(synced_lines)
        result_plain = "\n".join(plain_lines)
        result_bilingual = "\n".join(bilingual_lines)

        return {
            "synced": result_synced,
            "plain": result_plain,
            "bilingual": result_bilingual,
            "persian_synced": result_synced if target_lang == "fa" else "",
            "persian_plain": result_plain if target_lang == "fa" else "",
        }

    async def translate_lyrics_to_persian(self, synced_lrc: str, plain_lyrics: str = "") -> Dict[str, str]:
        return await self.translate_lyrics(synced_lrc, plain_lyrics, target_lang="fa")

    async def search_audio_match(self, artist: str, title: str, duration_sec: int = 0) -> str:
        """
        Searches YouTube / YouTube Music for official audio matching the Spotify track.
        Returns the best matching YouTube URL.
        """
        def _search():
            queries = [
                f"ytsearch5:{artist} - {title} official audio",
                f"ytsearch5:{artist} {title} audio",
                f"ytsearch5:{artist} {title}",
            ]
            ydl_opts = self._get_base_ydl_opts()
            ydl_opts.update({
                'skip_download': True,
                'extract_flat': 'in_playlist',
            })

            best_match_id = None
            min_duration_diff = float('inf')

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                for query in queries:
                    try:
                        res = ydl.extract_info(query, download=False)
                        entries = res.get('entries', []) if res else []
                        for entry in entries:
                            if not entry:
                                continue
                            v_id = entry.get('id')
                            v_dur = entry.get('duration') or 0
                            if duration_sec > 0 and v_dur > 0:
                                diff = abs(v_dur - duration_sec)
                                if diff < min_duration_diff:
                                    min_duration_diff = diff
                                    best_match_id = v_id
                                    if diff <= 4:
                                        return f"https://www.youtube.com/watch?v={best_match_id}"
                            elif not best_match_id:
                                best_match_id = v_id

                        if best_match_id and min_duration_diff <= 10:
                            return f"https://www.youtube.com/watch?v={best_match_id}"
                    except Exception as e:
                        logger.debug(f"Search query '{query}' failed: {e}")

            if best_match_id:
                return f"https://www.youtube.com/watch?v={best_match_id}"

            raise ValueError(f"هیچ نسخه صوتی منطبقی در یوتیوب موزیک برای آهنگ '{title}' پیدا نشد.")

        return await asyncio.to_thread(_search)


spotify_service = SpotifyService()
