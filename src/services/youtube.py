"""
YouTube video metadata extraction and downloading using yt-dlp.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yt_dlp

from src.bot.config import settings
from src.utils.formatting import format_seconds

import time

logger = logging.getLogger(__name__)

YOUTUBE_URL_REGEX = re.compile(
    r'^(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?.*v=|shorts/|embed/|live/)|youtu\.be/)([\w-]{11})([?&].*)?$',
    re.IGNORECASE
)

YOUTUBE_PLAYLIST_REGEX = re.compile(
    r'^(https?://)?(www\.|m\.|music\.)?youtube\.com/playlist\?.*[?&]list=([a-zA-Z0-9_-]+)',
    re.IGNORECASE
)

# In-memory TTL cache for YouTube video & playlist metadata (5 minutes)
_YOUTUBE_INFO_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL: float = 300.0


def extract_video_id(url: str) -> Optional[str]:
    match = YOUTUBE_URL_REGEX.search(url.strip())
    if match:
        return match.group(5)
    return None


def extract_playlist_id(url: str) -> Optional[str]:
    clean = url.strip()
    match = YOUTUBE_PLAYLIST_REGEX.search(clean)
    if match:
        return match.group(3)
    if "youtube.com" in clean or "youtu.be" in clean:
        param_match = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', clean)
        if param_match:
            return param_match.group(1)
    return None


def is_valid_youtube_playlist_url(url: str) -> bool:
    clean = url.strip()
    if YOUTUBE_PLAYLIST_REGEX.search(clean):
        return True
    if ("youtube.com/playlist" in clean or "music.youtube.com/playlist" in clean) and extract_playlist_id(clean):
        return True
    return False


def is_valid_youtube_url(url: str) -> bool:
    return extract_video_id(url) is not None or is_valid_youtube_playlist_url(url)


class YouTubeService:
    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url

    def _get_base_ydl_opts(self, is_playlist: bool = False) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'noplaylist': not is_playlist,
            'socket_timeout': 30,
            'retries': 15,
            'fragment_retries': 15,
            'extractor_retries': 5,
            'http_chunk_size': 10485760,
            'concurrent_fragment_downloads': 4,
            'ffmpeg_location': settings.ffmpeg_dir,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web']
                }
            },
        }
        active_proxy = self.proxy_url or settings.get_active_proxy()
        if active_proxy:
            opts['proxy'] = active_proxy

        if settings.node_path and Path(settings.node_path).exists():
            opts['js_runtimes'] = {'node': {'path': str(settings.node_path)}}

        return opts

    def _sync_extract_info(self, url: str) -> Dict[str, Any]:
        opts = self._get_base_ydl_opts(is_playlist=False)
        opts['skip_download'] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise ValueError("امکان استخراج اطلاعات این ویدیو از یوتیوب وجود ندارد.")
            return info

    def _sync_extract_playlist_info(self, url: str) -> Dict[str, Any]:
        opts = self._get_base_ydl_opts(is_playlist=True)
        opts.update({
            'extract_flat': 'in_playlist',
            'skip_download': True,
            'ignoreerrors': True,
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise ValueError("امکان استخراج اطلاعات این پلی‌لیست از یوتیوب وجود ندارد.")
            return info

    async def get_playlist_info(self, url: str) -> Dict[str, Any]:
        """Async extraction of YouTube playlist metadata and entries list."""
        clean_url = url.strip()
        now = time.time()
        if clean_url in _YOUTUBE_INFO_CACHE:
            ts, cached_data = _YOUTUBE_INFO_CACHE[clean_url]
            if now - ts < _CACHE_TTL:
                return cached_data

        info = await asyncio.to_thread(self._sync_extract_playlist_info, clean_url)
        playlist_id = info.get('id') or extract_playlist_id(clean_url) or "playlist"
        title = info.get('title') or "پلی‌لیست یوتیوب"
        channel = info.get('uploader') or info.get('channel') or "YouTube"

        # Determine best thumbnail for playlist
        playlist_thumb = None
        if info.get('thumbnails'):
            playlist_thumb = info['thumbnails'][-1].get('url')
        elif info.get('thumbnail'):
            playlist_thumb = info.get('thumbnail')

        raw_entries = list(info.get('entries', []))
        videos = []
        for idx, entry in enumerate(raw_entries, 1):
            if not entry or not entry.get('id'):
                continue
            v_id = entry.get('id')
            v_title = entry.get('title') or f"Video {idx}"
            v_dur = entry.get('duration') or 0
            v_channel = entry.get('uploader') or entry.get('channel') or channel

            v_thumb = None
            if entry.get('thumbnails'):
                v_thumb = entry['thumbnails'][-1].get('url')
            elif entry.get('thumbnail'):
                v_thumb = entry.get('thumbnail')
            else:
                v_thumb = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

            videos.append({
                'id': v_id,
                'title': v_title,
                'channel': v_channel,
                'duration': format_seconds(v_dur),
                'duration_seconds': v_dur,
                'thumbnail': v_thumb,
                'url': f"https://www.youtube.com/watch?v={v_id}",
                'index': idx,
            })

        if not playlist_thumb and videos:
            playlist_thumb = videos[0]['thumbnail']

        result = {
            'playlist_id': playlist_id,
            'title': title,
            'channel': channel,
            'thumbnail': playlist_thumb,
            'total_videos': len(videos),
            'videos': videos,
        }

        _YOUTUBE_INFO_CACHE[clean_url] = (now, result)
        if len(_YOUTUBE_INFO_CACHE) > 50:
            oldest_key = min(_YOUTUBE_INFO_CACHE.keys(), key=lambda k: _YOUTUBE_INFO_CACHE[k][0])
            _YOUTUBE_INFO_CACHE.pop(oldest_key, None)

        return result

    async def get_info(self, url: str) -> Dict[str, Any]:
        """Async extraction of video metadata with accurate video+audio format estimates and TTL cache."""
        clean_url = url.strip()
        now = time.time()
        if clean_url in _YOUTUBE_INFO_CACHE:
            ts, cached_data = _YOUTUBE_INFO_CACHE[clean_url]
            if now - ts < _CACHE_TTL:
                return cached_data

        info = await asyncio.to_thread(self._sync_extract_info, clean_url)

        video_id = info.get('id') or extract_video_id(clean_url)
        title = info.get('title', 'Unknown Title')
        channel = info.get('uploader') or info.get('channel', 'Unknown Channel')
        duration = info.get('duration') or 0
        thumbnail = info.get('thumbnail')
        playlist_id = extract_playlist_id(clean_url)

        # Check subtitles availability
        subs = info.get('subtitles', {}) or {}
        auto_subs = info.get('automatic_captions', {}) or {}
        has_fa_sub = 'fa' in subs or 'fa' in auto_subs
        has_en_sub = 'en' in subs or 'en' in auto_subs

        # Format estimates (Video + Audio combined)
        formats = info.get('formats', [])
        available_qualities = self._estimate_qualities(formats, duration)

        result = {
            'video_id': video_id,
            'title': title,
            'channel': channel,
            'duration': duration,
            'thumbnail': thumbnail,
            'has_fa_sub': has_fa_sub,
            'has_en_sub': has_en_sub,
            'qualities': available_qualities,
            'playlist_id': playlist_id,
            'raw_info': info,
        }

        # Cache result
        _YOUTUBE_INFO_CACHE[clean_url] = (now, result)
        if len(_YOUTUBE_INFO_CACHE) > 50:
            # Prune oldest
            oldest_key = min(_YOUTUBE_INFO_CACHE.keys(), key=lambda k: _YOUTUBE_INFO_CACHE[k][0])
            _YOUTUBE_INFO_CACHE.pop(oldest_key, None)

        return result

    def _estimate_qualities(self, formats: list, duration: int = 0) -> Dict[str, Optional[int]]:
        """Find accurate format size estimates (video + audio) for 360, 480, 720, 1080."""
        qualities: Dict[str, Optional[int]] = {'360': None, '480': None, '720': None, '1080': None}

        # Find best audio size to add to video-only streams
        best_audio_size = 0
        for f in formats:
            if f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none'):
                a_size = f.get('filesize') or f.get('filesize_approx')
                if not a_size and duration and f.get('abr'):
                    a_size = int((f['abr'] * 1000 / 8) * duration)
                if a_size and a_size > best_audio_size:
                    best_audio_size = a_size

        for f in formats:
            height = f.get('height')
            if not height or f.get('vcodec') == 'none':
                continue

            size = f.get('filesize') or f.get('filesize_approx')
            if not size and duration:
                tbr = f.get('tbr') or f.get('vbr')
                if tbr:
                    size = int((tbr * 1000 / 8) * duration)

            if not size:
                continue

            # If stream is video-only, add best audio size
            total_size = size + (best_audio_size if f.get('acodec') == 'none' else 0)

            for q in ('360', '480', '720', '1080'):
                if height == int(q) or (height <= int(q) and qualities[q] is None):
                    if qualities[q] is None or total_size > qualities[q]:
                        qualities[q] = total_size

        return qualities

    def _sync_download_video(self, url: str, quality: str, out_template: str) -> str:
        opts = self._get_base_ydl_opts()
        format_selector = (
            f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={quality}]+bestaudio/"
            f"best[height<={quality}]/best"
        )
        opts.update({
            'format': format_selector,
            'outtmpl': out_template,
            'merge_output_format': 'mp4',
            'continuedl': True,
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        expected = out_template.replace('%(ext)s', 'mp4')
        if Path(expected).exists():
            return expected
        parent = Path(out_template).parent
        stem = Path(out_template).stem.split('%')[0]
        matches = list(parent.glob(f"{stem}*.mp4"))
        if matches:
            return str(matches[0])
        raise FileNotFoundError(f"Downloaded video not found for {out_template}")

    async def download_video(self, url: str, quality: str, video_id: str) -> Path:
        out_tmpl = str(settings.download_dir / f"{video_id}_{quality}p.%(ext)s")
        file_path_str = await asyncio.to_thread(self._sync_download_video, url, quality, out_tmpl)
        return Path(file_path_str)

    def _sync_download_audio(self, url: str, out_template: str) -> str:
        opts = self._get_base_ydl_opts()
        opts.update({
            'format': 'bestaudio/best',
            'outtmpl': out_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        expected = out_template.replace('%(ext)s', 'mp3')
        if Path(expected).exists():
            return expected
        parent = Path(out_template).parent
        stem = Path(out_template).stem.split('%')[0]
        matches = list(parent.glob(f"{stem}*.mp3"))
        if matches:
            return str(matches[0])
        raise FileNotFoundError(f"Downloaded audio not found for {out_template}")

    async def download_audio(self, url: str, video_id: str) -> Path:
        out_tmpl = str(settings.download_dir / f"{video_id}.%(ext)s")
        file_path_str = await asyncio.to_thread(self._sync_download_audio, url, out_tmpl)
        return Path(file_path_str)
