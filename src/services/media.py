"""
FFmpeg processing, MP3 ID3 metadata tagging, and video compression.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Tuple

def _get_subprocess_startupinfo():
    if os.name == 'nt':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None

import httpx
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TRCK, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus

from src.bot.config import settings

logger = logging.getLogger(__name__)

THUMBNAILS_CACHE_DIR = settings.data_dir / ".cache" / "thumbnails"
THUMBNAILS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

WAVEFORM_CACHE_DIR = settings.data_dir / ".cache" / "waveforms"
WAVEFORM_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class ThumbnailService:
    @staticmethod
    def get_cache_path(media_path: Path) -> Path:
        stat = media_path.stat()
        key_raw = f"{media_path.resolve()}_{stat.st_mtime}_{stat.st_size}"
        cache_hash = hashlib.sha256(key_raw.encode()).hexdigest()
        return THUMBNAILS_CACHE_DIR / f"{cache_hash}.jpg"

    @classmethod
    def _sync_get_or_create(cls, media_path: Path) -> Optional[Path]:
        if not media_path.exists() or not media_path.is_file():
            return None

        cache_file = cls.get_cache_path(media_path)
        if cache_file.exists() and cache_file.stat().st_size > 0:
            return cache_file

        # 1. Check companion image in same directory (e.g. song.jpg, song.png)
        for ext in ('.jpg', '.jpeg', '.png', '.webp'):
            companion = media_path.with_suffix(ext)
            if companion.exists() and companion.is_file() and companion.stat().st_size > 0:
                try:
                    cache_file.write_bytes(companion.read_bytes())
                    return cache_file
                except Exception:
                    return companion

        ext = media_path.suffix.lower()

        # 2. Extract embedded cover from audio files
        if ext == '.mp3':
            try:
                tags = ID3(str(media_path))
                for key in tags.keys():
                    if key.startswith('APIC'):
                        apic: APIC = tags[key]
                        cache_file.write_bytes(apic.data)
                        return cache_file
            except Exception as e:
                logger.debug(f"ID3 APIC extraction failed for {media_path}: {e}")

        elif ext == '.m4a':
            try:
                mp4 = MP4(str(media_path))
                covr = mp4.tags.get('covr') if mp4.tags else None
                if covr and len(covr) > 0:
                    cache_file.write_bytes(bytes(covr[0]))
                    return cache_file
            except Exception as e:
                logger.debug(f"M4A covr extraction failed for {media_path}: {e}")

        elif ext == '.flac':
            try:
                flac = FLAC(str(media_path))
                if flac.pictures:
                    cache_file.write_bytes(flac.pictures[0].data)
                    return cache_file
            except Exception as e:
                logger.debug(f"FLAC picture extraction failed for {media_path}: {e}")

        elif ext in ('.opus', '.ogg'):
            try:
                opus = OggOpus(str(media_path))
                if 'metadata_block_picture' in opus and opus['metadata_block_picture']:
                    for block in opus['metadata_block_picture']:
                        p = Picture(base64.b64decode(block))
                        if p.data:
                            cache_file.write_bytes(p.data)
                            return cache_file
            except Exception as e:
                logger.debug(f"Opus picture extraction failed for {media_path}: {e}")

        # 3. Extract video frame via FFmpeg
        if ext in ('.mp4', '.mkv', '.webm', '.mov', '.avi'):
            ffmpeg_exe = settings.ffmpeg_path
            cmd = [
                ffmpeg_exe, '-y',
                '-ss', '3',
                '-i', str(media_path),
                '-vframes', '1',
                '-vf', 'scale=320:-1',
                '-q:v', '3',
                str(cache_file)
            ]
            try:
                subprocess.run(cmd, capture_output=True, check=True, startupinfo=_get_subprocess_startupinfo())
                if cache_file.exists() and cache_file.stat().st_size > 0:
                    return cache_file
            except Exception as e:
                logger.debug(f"Video frame extraction failed for {media_path}: {e}")

        return None

    @classmethod
    async def get_or_create_thumbnail(cls, media_path: Path) -> Optional[Path]:
        return await asyncio.to_thread(cls._sync_get_or_create, media_path)

    get_thumbnail = get_or_create_thumbnail


class WaveformService:
    @staticmethod
    def get_cache_path(media_path: Path) -> Path:
        stat = media_path.stat()
        key_raw = f"{media_path.resolve()}_{stat.st_mtime}_{stat.st_size}"
        cache_hash = hashlib.sha256(key_raw.encode()).hexdigest()
        return WAVEFORM_CACHE_DIR / f"{cache_hash}.json"

    @classmethod
    def _sync_extract_peaks(cls, media_path: Path, samples: int = 120) -> list[float]:
        if not media_path.exists() or not media_path.is_file():
            return [0.35] * samples

        cache_file = cls.get_cache_path(media_path)
        if cache_file.exists() and cache_file.stat().st_size > 0:
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "peaks" in data and len(data["peaks"]) == samples:
                    return data["peaks"]
            except Exception:
                pass

        duration = 240.0
        ffprobe_exe = settings.ffprobe_path
        if Path(ffprobe_exe).exists():
            probe_cmd = [
                ffprobe_exe,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(media_path)
            ]
            try:
                out = subprocess.check_output(
                    probe_cmd,
                    stderr=subprocess.DEVNULL,
                    startupinfo=_get_subprocess_startupinfo()
                ).decode("utf-8", errors="ignore")
                info = json.loads(out)
                duration = float(info.get("format", {}).get("duration", 240.0))
            except Exception:
                duration = 240.0

        reset_interval = max(1, int(duration / samples))
        ffmpeg_exe = settings.ffmpeg_path
        cmd = [
            ffmpeg_exe,
            "-i", str(media_path),
            "-af", f"aresample=8000,astats=metadata=1:reset={reset_interval}",
            "-f", "null", "-"
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                startupinfo=_get_subprocess_startupinfo()
            )
            rms_pattern = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+\.?\d*)")
            values = [float(m) for m in rms_pattern.findall(proc.stderr) if float(m) > -120]
        except Exception as e:
            logger.warning(f"Failed to run ffmpeg astats: {e}")
            values = []

        if not values:
            peaks = [0.35] * samples
        else:
            min_v, max_v = min(values), max(values)
            span = max_v - min_v if max_v != min_v else 1.0
            normalized = [round(max(0.08, min(1.0, (v - min_v) / span)), 3) for v in values]
            if len(normalized) != samples:
                step = len(normalized) / samples
                peaks = [normalized[min(len(normalized) - 1, int(i * step))] for i in range(samples)]
            else:
                peaks = normalized

        # Cache cleanup: if > 500 files, remove oldest
        try:
            caches = list(WAVEFORM_CACHE_DIR.glob("*.json"))
            if len(caches) > 500:
                caches.sort(key=lambda p: p.stat().st_mtime)
                for old in caches[:len(caches) - 450]:
                    try:
                        old.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            cache_file.write_text(json.dumps({"peaks": peaks}), encoding="utf-8")
        except Exception as err:
            logger.debug(f"Could not save waveform cache: {err}")

        return peaks

    @classmethod
    async def get_or_create_waveform(cls, media_path: Path, samples: int = 120) -> list[float]:
        return await asyncio.to_thread(cls._sync_extract_peaks, media_path, samples)


class MediaService:
    def __init__(self, ffmpeg_path: str = settings.ffmpeg_path):
        self.ffmpeg_path = ffmpeg_path

    def _sync_probe_video(self, video_path: Path) -> Tuple[int, int, int]:
        cmd = [
            settings.ffprobe_path, '-v', 'error',
            '-show_entries', 'stream=width,height:format=duration',
            '-of', 'json', str(video_path)
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True, startupinfo=_get_subprocess_startupinfo())
            data = json.loads(proc.stdout)
            streams = [s for s in data.get('streams', []) if 'width' in s]
            width = streams[0]['width'] if streams else 1280
            height = streams[0]['height'] if streams else 720
            duration = int(float(data.get('format', {}).get('duration', 0)))
            return width, height, duration
        except Exception as e:
            logger.warning(f"ffprobe failed for {video_path}: {e}")
            return 1280, 720, 0

    async def get_video_metadata(self, video_path: Path) -> Tuple[int, int, int]:
        """Returns (width, height, duration_seconds) using ffprobe."""
        return await asyncio.to_thread(self._sync_probe_video, video_path)

    async def extract_thumbnail(self, video_path: Path, thumb_path: Path, at_second: int = 3) -> Optional[Path]:
        """Extract a single frame to use as thumbnail."""
        def _extract():
            cmd = [
                self.ffmpeg_path, '-y',
                '-ss', str(at_second),
                '-i', str(video_path),
                '-vframes', '1',
                '-q:v', '2',
                str(thumb_path)
            ]
            try:
                subprocess.run(cmd, capture_output=True, check=True, startupinfo=_get_subprocess_startupinfo())
                if thumb_path.exists():
                    return thumb_path
            except Exception as e:
                logger.warning(f"Thumbnail extraction failed: {e}")
            return None

        return await asyncio.to_thread(_extract)

    async def compress_video(self, video_path: Path, target_size_mb: int = 48) -> Path:
        """
        Compress video using bitrate-constrained x264 to fit under target_size_mb.
        Preserves video dimensions and aspect ratio.
        """
        def _compress():
            file_size_mb = video_path.stat().st_size / (1024 * 1024)
            if file_size_mb <= target_size_mb:
                return video_path

            _, _, duration = self._sync_probe_video(video_path)
            if duration <= 0:
                duration = 60

            target_total_bps = (target_size_mb * 1024 * 1024 * 8) / duration
            audio_bps = 128 * 1000
            video_bps = max(int(target_total_bps - audio_bps), 100 * 1000)

            compressed_path = video_path.parent / f"compressed_{video_path.name}"
            cmd = [
                self.ffmpeg_path, '-y',
                '-i', str(video_path),
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-b:v', f"{video_bps}",
                '-maxrate', f"{int(video_bps * 1.3)}",
                '-bufsize', f"{int(video_bps * 2)}",
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                str(compressed_path)
            ]
            logger.info(f"Compressing {video_path.name} ({file_size_mb:.1f}MB) -> target {target_size_mb}MB...")
            subprocess.run(cmd, capture_output=True, check=True, startupinfo=_get_subprocess_startupinfo())

            if compressed_path.exists() and compressed_path.stat().st_size > 0:
                return compressed_path
            return video_path

        return await asyncio.to_thread(_compress)

    async def tag_mp3(
        self,
        mp3_path: Path,
        title: str,
        artist: str,
        album: Optional[str] = None,
        year: Optional[str] = None,
        track_num: Optional[int] = None,
        lyrics: Optional[str] = None,
        cover_image: Optional[Path] = None,
        cover_url: Optional[str] = None,
    ) -> None:
        """Inject complete ID3 tags, lyrics, and high-resolution cover into MP3 file."""
        cover_data = None
        mime_type = 'image/jpeg'

        if cover_image and cover_image.exists():
            cover_data = cover_image.read_bytes()
            mime_type = 'image/jpeg' if cover_image.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
        elif cover_url:
            try:
                proxy_url = settings.get_active_proxy()
                async with httpx.AsyncClient(proxy=proxy_url, timeout=12.0, follow_redirects=True) as client:
                    resp = await client.get(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
                    if resp.status_code == 200:
                        cover_data = resp.content
                        ct = resp.headers.get('Content-Type', '')
                        if 'png' in ct:
                            mime_type = 'image/png'
            except Exception as e:
                logger.warning(f"Could not download cover art from {cover_url}: {e}")

        def _tag():
            try:
                audio = MP3(str(mp3_path), ID3=ID3)
                try:
                    audio.add_tags()
                except Exception:
                    pass

                audio.tags.add(TIT2(encoding=3, text=title))
                audio.tags.add(TPE1(encoding=3, text=artist))

                if album:
                    audio.tags.add(TALB(encoding=3, text=album))

                if year:
                    audio.tags.add(TDRC(encoding=3, text=str(year)))

                if track_num:
                    audio.tags.add(TRCK(encoding=3, text=str(track_num)))

                if lyrics:
                    audio.tags.add(USLT(encoding=3, lang='eng', desc='Lyrics', text=lyrics))

                if cover_data:
                    audio.tags.add(APIC(
                        encoding=3,
                        mime=mime_type,
                        type=3,
                        desc='Cover',
                        data=cover_data
                    ))

                audio.save(v2_version=3)
                logger.info(f"Tagged MP3 (ID3v2.3) successfully: {mp3_path.name}")
            except Exception as e:
                logger.warning(f"Could not tag MP3 {mp3_path}: {e}")

        await asyncio.to_thread(_tag)

    async def tag_opus(
        self,
        opus_path: Path,
        title: str,
        artist: str,
        album: Optional[str] = None,
        year: Optional[str] = None,
        track_num: Optional[int] = None,
        cover_image: Optional[Path] = None,
        cover_url: Optional[str] = None,
    ) -> None:
        """Inject Vorbis comments and embedded album art into Opus/OGG container."""
        cover_data = None
        mime_type = 'image/jpeg'

        if cover_image and cover_image.exists():
            cover_data = cover_image.read_bytes()
            mime_type = 'image/jpeg' if cover_image.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
        elif cover_url:
            try:
                proxy_url = settings.get_active_proxy()
                async with httpx.AsyncClient(proxy=proxy_url, timeout=12.0, follow_redirects=True) as client:
                    resp = await client.get(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
                    if resp.status_code == 200:
                        cover_data = resp.content
                        ct = resp.headers.get('Content-Type', '')
                        if 'png' in ct:
                            mime_type = 'image/png'
            except Exception as e:
                logger.warning(f"Could not download cover art from {cover_url}: {e}")

        def _tag():
            try:
                audio = OggOpus(str(opus_path))
                audio['title'] = [title]
                audio['artist'] = [artist]
                if album:
                    audio['album'] = [album]
                if year:
                    audio['date'] = [str(year)]
                if track_num:
                    audio['tracknumber'] = [str(track_num)]

                if cover_data:
                    p = Picture()
                    p.data = cover_data
                    p.type = 3  # front cover
                    p.mime = mime_type
                    p.desc = 'Cover'
                    encoded = base64.b64encode(p.write()).decode('ascii')
                    audio['metadata_block_picture'] = [encoded]

                audio.save()
                logger.info(f"Tagged Opus successfully: {opus_path.name}")
            except Exception as e:
                logger.warning(f"Could not tag Opus {opus_path}: {e}")

        await asyncio.to_thread(_tag)

