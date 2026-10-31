"""
Background task manager for downloading with real-time progress tracking.
Supports YouTube videos, YouTube audio/subtitles, and Spotify tracks/albums/lyrics.
"""
import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yt_dlp

from src.bot.config import settings
from src.services.media import MediaService
from src.services.spotify import spotify_service
from src.services.subtitles import SubtitleService
from src.services.youtube import YouTubeService, extract_video_id
from src.utils.formatting import format_bytes, sanitize_filename

logger = logging.getLogger(__name__)

_VIDEO_ID_PATTERN = re.compile(r'\[([a-zA-Z0-9_-]{11})\]')


def _get_existing_video_ids(target_dir: Path) -> set[str]:
    """Scans playlist directory for files tagged with [video_id]."""
    existing = set()
    if not target_dir.exists():
        return existing
    for f in target_dir.iterdir():
        if f.is_file():
            m = _VIDEO_ID_PATTERN.search(f.name)
            if m:
                existing.add(m.group(1))
    return existing


class DownloadTaskManager:
    def __init__(self):
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self._async_tasks: Dict[str, asyncio.Task] = {}
        self.media_service = MediaService()
        self.subtitle_service = SubtitleService()
        self.youtube_service = YouTubeService()

    def register_async_task(self, task_id: str, async_task: asyncio.Task) -> None:
        self._async_tasks[task_id] = async_task

    def cancel_task(self, task_id: str) -> bool:
        cancelled = False
        if task_id in self.tasks:
            t = self.tasks[task_id]
            t["is_cancelled"] = True
            t["is_done"] = True
            t["status"] = "🛑 دانلود متوقف شد"
            t["error"] = "دانلود توسط کاربر متوقف شد."
            cancelled = True
        if task_id in self._async_tasks:
            task = self._async_tasks[task_id]
            if not task.done():
                task.cancel()
            cancelled = True
        return cancelled

    def create_task(
        self,
        url: str,
        action_type: str,
        quality: str = "720",
        lang: str = "fa",
        extra: Optional[Dict[str, Any]] = None
    ) -> str:
        task_id = str(uuid.uuid4())[:8]

        initial_title = "در حال دانلود..."
        if extra:
            if extra.get("playlist_title"):
                fmt_lbl = "ویدیوهای" if extra.get("format_type") == "video" else "صوت‌های"
                initial_title = f"پلی‌لیست یوتیوب ({fmt_lbl}): {extra.get('playlist_title')}"
            elif extra.get("title") and extra.get("artist"):
                initial_title = f"{extra.get('artist')} - {extra.get('title')}"
            elif extra.get("title"):
                initial_title = extra.get("title")
        elif "spotify" in action_type:
            initial_title = "آهنگ اسپاتیفای"
        elif "youtube_batch" in action_type:
            initial_title = "پلی‌لیست یوتیوب"
        else:
            initial_title = "مدیای یوتیوب"

        self.tasks[task_id] = {
            "id": task_id,
            "title": initial_title,
            "url": url,
            "type": action_type,
            "quality": quality,
            "lang": lang,
            "extra": extra or {},
            "status": "در حال آماده‌سازی...",
            "progress": 0.0,
            "speed": "0 B/s",
            "eta": "--",
            "downloaded": "0 B",
            "total": "0 B",
            "filename": None,
            "filepath": None,
            "error": None,
            "is_done": False,
            "created_at": time.time(),
        }
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.tasks.get(task_id)

    def get_all_tasks(self, include_done: bool = True, limit: int = 20) -> List[Dict[str, Any]]:
        tasks = list(self.tasks.values())
        tasks.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        if not include_done:
            tasks = [t for t in tasks if not t.get("is_done")]
        return tasks[:limit]

    def dismiss_task(self, task_id: str) -> bool:
        self._async_tasks.pop(task_id, None)
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False

    def _make_progress_hook(self, task_id: str) -> Callable[[Dict[str, Any]], None]:
        def hook(d: Dict[str, Any]):
            t = self.tasks.get(task_id)
            if not t:
                return

            if t.get("is_cancelled"):
                raise yt_dlp.utils.DownloadCancelled("دانلود توسط کاربر لغو شد.")

            status = d.get('status')
            if status == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                speed = d.get('speed') or 0
                eta = d.get('eta')

                percent = (downloaded / total * 100) if total > 0 else 0.0
                t["progress"] = round(min(percent, 99.9), 1)
                t["speed"] = f"{format_bytes(speed)}/s" if speed else "0 B/s"
                t["eta"] = f"{int(eta)} ثانیه" if eta else "--"
                t["downloaded"] = format_bytes(downloaded)
                t["total"] = format_bytes(total) if total else "نامشخص"
                t["status"] = "در حال دانلود فایل..."

            elif status == 'finished':
                t["status"] = "در حال پردازش، تبدیل فرمت و تگ‌گذاری..."
                t["progress"] = 99.9

        return hook

    def _get_common_ydl_opts(self, out_tmpl: str, hook: Optional[Callable] = None, ratelimit_kbps: int = 0) -> Dict[str, Any]:
        """تنظیمات پایه و مقاوم در برابر قطعی اینترنت و فیلترشکن برای yt-dlp"""
        opts: Dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': out_tmpl,
            'ffmpeg_location': settings.ffmpeg_dir,
            'nocheckcertificate': True,
            'continuedl': True,
            # تنظیمات جلوگیری از خطای ConnectionResetError (10054) در فایل‌های حجیم:
            'retries': 25,
            'fragment_retries': 25,
            'file_access_retries': 10,
            'extractor_retries': 10,
            'socket_timeout': 30,
            'http_chunk_size': 10485760,  # دانلود در قطعات 10 مگابایتی برای جلوگیری از ریست شدن کانکشن
            'concurrent_fragment_downloads': 4,  # دانلود همزمان چند قطعه برای افزایش پایداری و سرعت
            'retry_sleep_functions': {
                'http': lambda n: min(2 * n, 10),
                'fragment': lambda n: min(2 * n, 10),
                'extractor': lambda n: 3,
            },
        }
        if hook:
            opts['progress_hooks'] = [hook]

        if ratelimit_kbps and ratelimit_kbps > 0:
            opts['ratelimit'] = int(ratelimit_kbps) * 1024

        active_proxy = settings.get_active_proxy()
        if active_proxy:
            opts['proxy'] = active_proxy

        if settings.node_path and Path(settings.node_path).exists():
            opts['js_runtimes'] = {'node': {'path': str(settings.node_path)}}

        return opts

    async def _save_companion_transcripts(self, url: str, video_id: str, media_file: Optional[Path]) -> None:
        """Fetches YouTube subtitles/transcript (English & Persian) and saves companion .lrc files for synced lyrics/podcast view."""
        if not video_id or not media_file or not media_file.exists():
            return
        try:
            from src.services.spotify import spotify_service

            # 1. Fetch transcript in English/Original
            en_text, _ = await asyncio.to_thread(self.subtitle_service._sync_fetch_transcript, url, "en", video_id)
            # 2. Fetch transcript in Persian (with auto-translation)
            fa_text, _ = await asyncio.to_thread(self.subtitle_service._sync_fetch_transcript, url, "fa", video_id)

            # Bidirectional cross-translation if one is missing:
            if en_text and en_text.strip() and (not fa_text or not fa_text.strip()):
                try:
                    trans_fa = await spotify_service.translate_lyrics(en_text, "", target_lang="fa")
                    fa_text = trans_fa.get("synced") or trans_fa.get("plain", "")
                except Exception as t_err:
                    logger.debug(f"Auto-translate EN to FA failed: {t_err}")

            elif fa_text and fa_text.strip() and (not en_text or not en_text.strip()):
                try:
                    trans_en = await spotify_service.translate_lyrics(fa_text, "", target_lang="en")
                    en_text = trans_en.get("synced") or trans_en.get("plain", "")
                except Exception as t_err:
                    logger.debug(f"Auto-translate FA to EN failed: {t_err}")

            # If still no transcript (common for music videos), fallback to multi-provider lyrics service
            if (not en_text or not en_text.strip()) and (not fa_text or not fa_text.strip()):
                try:
                    from src.services.lyrics import lyrics_service
                    lyr = await lyrics_service.get_lyrics(media_path=media_file, title=media_file.stem, include_translation=True)
                    if lyr.get("has_lyrics"):
                        en_text = lyr.get("synced_lyrics") or lyr.get("plain_lyrics", "")
                        fa_text = lyr.get("persian_synced") or lyr.get("persian_plain", "")
                except Exception as lyr_err:
                    logger.debug(f"Music video lyrics fallback failed: {lyr_err}")

            if en_text and en_text.strip():
                lrc_path = media_file.with_suffix(".lrc")
                lrc_path.write_text(en_text, encoding="utf-8")

            if fa_text and fa_text.strip():
                fa_lrc_path = media_file.with_name(f"{media_file.stem}_persian.lrc")
                fa_lrc_path.write_text(fa_text, encoding="utf-8")

            # Fallback guarantee: if only one exists, make sure primary .lrc is populated
            lrc_path = media_file.with_suffix(".lrc")
            if not lrc_path.exists():
                if fa_text and fa_text.strip():
                    lrc_path.write_text(fa_text, encoding="utf-8")
        except Exception as e:
            logger.debug(f"Companion transcript auto-fetch failed for {video_id}: {e}")

    async def execute_task(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return

        url = t["url"]
        action_type = t["type"]
        quality = t["quality"]
        lang = t["lang"]
        extra = t.get("extra", {})
        hook = self._make_progress_hook(task_id)

        try:
            # -------------------------------------------------------------
            # SPOTIFY: Single Audio Download (MP3 320k/192k + Cover + ID3)
            # -------------------------------------------------------------
            if action_type == "spotify_audio":
                bitrate = quality if quality in ("320", "192") else "320"
                t["status"] = "در حال استخراج متادیتای قطعه از اسپاتیفای..."

                track_url = extra.get("url") or url
                track_id = extra.get("track_id")
                is_from_playlist = extra.get("is_playlist", False)

                # If track was invoked from a playlist or missing cover, resolve individual track metadata
                if is_from_playlist or not extra.get("cover"):
                    try:
                        # Full individual track metadata provides authentic album name & 640x640 cover
                        track_meta = await spotify_service.get_metadata(track_url)
                        title = track_meta.get("title") or extra.get("title", "Audio")
                        artist = track_meta.get("artist") or extra.get("artist", "Spotify")
                        album = track_meta.get("album") or extra.get("collection_name") or title
                        year = track_meta.get("release_year") or extra.get("release_year")
                        cover_url = track_meta.get("cover") or await spotify_service.get_track_cover(track_id or track_url) or extra.get("collection_cover")
                        duration_sec = track_meta.get("duration_seconds") or extra.get("duration_seconds", 0)
                    except Exception as e:
                        logger.debug(f"Direct track metadata fetch fallback for {track_url}: {e}")
                        title = extra.get("title", "Audio")
                        artist = extra.get("artist", "Spotify")
                        album = extra.get("album") or extra.get("collection_name") or title
                        year = extra.get("release_year")
                        cover_url = await spotify_service.get_track_cover(track_id or track_url) or extra.get("collection_cover") or extra.get("cover")
                        duration_sec = extra.get("duration_seconds", 0)
                else:
                    meta = extra if (extra and extra.get("title")) else await spotify_service.get_metadata(url)
                    title = meta.get("title", "Audio")
                    artist = meta.get("artist", "Spotify")
                    album = meta.get("album", title)
                    year = meta.get("release_year")
                    cover_url = meta.get("cover")
                    duration_sec = meta.get("duration_seconds", 0)

                t["title"] = f"{artist} - {title}"

                safe_name = sanitize_filename(f"{artist} - {title}")
                t["status"] = f"در حال جستجوی نسخه رسمی در یوتیوب موزیک ({artist} - {title})..."

                matched_url = await spotify_service.search_audio_match(artist, title, duration_sec)
                t["status"] = f"در حال دانلود صوت با کیفیت MP3 {bitrate}kbps..."

                out_tmpl = str(settings.download_dir / f"{safe_name}.%(ext)s")

                def _download_spotify_audio():
                    opts = self._get_common_ydl_opts(out_tmpl, hook)
                    opts.update({
                        'format': 'bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': bitrate,
                        }],
                    })
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([matched_url])

                await asyncio.to_thread(_download_spotify_audio)

                mp3_file = Path(out_tmpl.replace('%(ext)s', 'mp3'))
                if not mp3_file.exists():
                    matches = list(settings.download_dir.glob(f"{safe_name}*.mp3"))
                    mp3_file = matches[0] if matches else None

                if not mp3_file or not mp3_file.exists():
                    raise FileNotFoundError("فایل صوتی دانلود نشد.")

                # Fetch lyrics from multi-provider service (LRCLIB + syncedlyrics + Persian translation)
                t["status"] = "در حال دریافت لیریکس و درج کاور اورجینال اسپاتیفای..."
                lyrics_text = None
                try:
                    from src.services.lyrics import lyrics_service
                    lyrics_data = await lyrics_service.get_lyrics(
                        media_path=mp3_file,
                        title=title,
                        artist=artist,
                        album=album,
                        duration=duration_sec,
                        include_translation=True
                    )
                    lyrics_text = lyrics_data.get("plain_lyrics") or lyrics_data.get("synced_lyrics")

                    # If synced lyrics exist, save companion .lrc file matching mp3 name
                    if lyrics_data.get("synced_lyrics"):
                        mp3_file.with_suffix(".lrc").write_text(lyrics_data["synced_lyrics"], encoding="utf-8")

                    # Also save Persian translated synced lyrics
                    if lyrics_data.get("persian_synced"):
                        mp3_file.with_name(f"{mp3_file.stem}_persian.lrc").write_text(
                            lyrics_data["persian_synced"], encoding="utf-8"
                        )
                except Exception as lyr_err:
                    logger.debug(f"Companion lyrics download failed: {lyr_err}")

                await self.media_service.tag_mp3(
                    mp3_path=mp3_file,
                    title=title,
                    artist=artist,
                    album=album,
                    year=year,
                    track_num=extra.get("track_num"),
                    lyrics=lyrics_text,
                    cover_url=cover_url
                )

                t["filepath"] = str(mp3_file)
                t["filename"] = mp3_file.name
                t["total"] = format_bytes(mp3_file.stat().st_size)
                t["progress"] = 100.0
                t["status"] = f"✅ موزیک با کیفیت MP3 {bitrate}kbps و کاور رسمی آماده شد!"
                t["is_done"] = True

            # -------------------------------------------------------------
            # SPOTIFY: Lyrics extraction (.lrc, Persian, Bilingual)
            # -------------------------------------------------------------
            elif action_type == "spotify_lyrics":
                t["status"] = "در حال واکشی لیریکس از دیتابیس..."
                meta = extra if (extra and extra.get("title")) else await spotify_service.get_metadata(url)
                title = meta.get("title", "Track")
                artist = meta.get("artist", "Artist")
                duration_sec = meta.get("duration_seconds", 0)
                t["title"] = f"لیریکس: {artist} - {title}"
                safe_name = sanitize_filename(f"{artist} - {title}")

                lyrics = await spotify_service.get_lyrics(title, artist, duration_sec)
                if not lyrics.get("has_lyrics"):
                    raise ValueError(f"متأسفانه متنی برای آهنگ '{title}' در دیتابیس پیدا نشد.")

                t["status"] = "در حال ترجمه هوشمند خط‌به‌خط به فارسی..."
                trans = await spotify_service.translate_lyrics_to_persian(
                    lyrics.get("synced_lyrics", ""),
                    lyrics.get("plain_lyrics", "")
                )

                # Save original LRC
                lrc_file = settings.download_dir / f"{safe_name}.lrc"
                if lyrics.get("synced_lyrics"):
                    lrc_file.write_text(lyrics["synced_lyrics"], encoding="utf-8")

                # Save Persian synced LRC
                if trans.get("persian_synced"):
                    fa_lrc = settings.download_dir / f"{safe_name}_persian.lrc"
                    fa_lrc.write_text(trans["persian_synced"], encoding="utf-8")

                # Save Bilingual TXT
                bilingual_file = settings.download_dir / f"{safe_name}_bilingual.txt"
                bilingual_content = (
                    f"============================================================\n"
                    f"🎵 متن و ترجمه فارسی آهنگ: {artist} - {title}\n"
                    f"============================================================\n\n"
                    f"{trans.get('bilingual', '')}\n\n"
                    f"============================================================\n"
                )
                bilingual_file.write_text(bilingual_content, encoding="utf-8")

                target_file = bilingual_file if lang == "bilingual" else (lrc_file if lrc_file.exists() else bilingual_file)
                t["filepath"] = str(target_file)
                t["filename"] = target_file.name
                t["total"] = format_bytes(target_file.stat().st_size)
                t["progress"] = 100.0
                t["status"] = "✅ فایل‌های لیریکس (انگلیسی، فارسی و دوزبانه) ذخیره شدند!"
                t["is_done"] = True

            # -------------------------------------------------------------
            # SPOTIFY: Batch Album / Playlist Download
            # -------------------------------------------------------------
            elif action_type == "spotify_batch":
                meta = extra if (extra and extra.get("tracks")) else await spotify_service.get_metadata(url)
                tracks = meta.get("tracks", [])
                total_tracks = len(tracks)
                if not tracks:
                    raise ValueError("هیچ قطعه‌ای در این آلبوم یا پلی‌لیست یافت نشد.")

                bitrate = quality if quality in ("320", "192") else "320"
                collection_cover = meta.get("cover")
                collection_name = meta.get("title", "Collection")
                is_playlist = meta.get("entity_type") == "playlist"
                t["title"] = f"{'پلی‌لیست' if is_playlist else 'آلبوم'} اسپاتیفای: {collection_name}"

                downloaded_count = 0
                for idx, trk in enumerate(tracks, 1):
                    if t.get("is_cancelled"):
                        logger.info(f"Spotify batch task {task_id} aborted by user.")
                        break

                    t_title = trk.get("title", f"Track {idx}")
                    t_artist = trk.get("artist") or meta.get("artist", "Artist")
                    t_dur = trk.get("duration_seconds", 0)
                    t_id = trk.get("id")
                    t_url = trk.get("url") or (f"https://open.spotify.com/track/{t_id}" if t_id else None)

                    # Distinct cover & album resolution:
                    # In a playlist, each track comes from its own album/single and has its own cover!
                    # In an album, all tracks share the album cover and album name.
                    t_cover = collection_cover
                    t_album = collection_name
                    t_year = None

                    if is_playlist:
                        t["status"] = f"در حال استخراج کاور اختصاصی قطعه {idx} از {total_tracks}: {t_artist} - {t_title}"
                        # 1. Try fast individual 640x640 cover
                        if t_id:
                            try:
                                ind_cover = await spotify_service.get_track_cover(t_id)
                                if ind_cover:
                                    t_cover = ind_cover
                            except Exception as e:
                                logger.debug(f"Track cover fetch failed for {t_id}: {e}")

                        # 2. Try fetching true album name and year if track URL is available
                        if t_url:
                            try:
                                track_meta = await spotify_service.get_metadata(t_url)
                                if track_meta.get("album"):
                                    t_album = track_meta["album"]
                                if track_meta.get("release_year"):
                                    t_year = track_meta["release_year"]
                                if track_meta.get("cover"):
                                    t_cover = track_meta["cover"]
                            except Exception:
                                pass

                    t["status"] = f"در حال دانلود قطعه {idx} از {total_tracks}: {t_artist} - {t_title}"
                    t["progress"] = round(((idx - 1) / total_tracks) * 100, 1)

                    try:
                        matched_yt = await spotify_service.search_audio_match(t_artist, t_title, t_dur)
                        safe_name = sanitize_filename(f"{t_artist} - {t_title}")
                        out_tmpl = str(settings.download_dir / f"{safe_name}.%(ext)s")

                        def _down_single():
                            opts = self._get_common_ydl_opts(out_tmpl, None)
                            opts.update({
                                'format': 'bestaudio/best',
                                'postprocessors': [{
                                    'key': 'FFmpegExtractAudio',
                                    'preferredcodec': 'mp3',
                                    'preferredquality': bitrate,
                                }],
                            })
                            with yt_dlp.YoutubeDL(opts) as ydl:
                                ydl.download([matched_yt])

                        await asyncio.to_thread(_down_single)

                        f_mp3 = Path(out_tmpl.replace('%(ext)s', 'mp3'))
                        if f_mp3.exists():
                            lyrics_text = None
                            try:
                                from src.services.lyrics import lyrics_service
                                lyrics_data = await lyrics_service.get_lyrics(
                                    media_path=f_mp3,
                                    title=t_title,
                                    artist=t_artist,
                                    album=t_album,
                                    duration=t_dur,
                                    include_translation=True
                                )
                                lyrics_text = lyrics_data.get("plain_lyrics") or lyrics_data.get("synced_lyrics")
                                if lyrics_data.get("synced_lyrics"):
                                    f_mp3.with_suffix(".lrc").write_text(lyrics_data["synced_lyrics"], encoding="utf-8")
                                if lyrics_data.get("persian_synced"):
                                    f_mp3.with_name(f"{f_mp3.stem}_persian.lrc").write_text(
                                        lyrics_data["persian_synced"], encoding="utf-8"
                                    )
                            except Exception as lyr_err:
                                logger.debug(f"Album track companion lyrics failed: {lyr_err}")

                            await self.media_service.tag_mp3(
                                mp3_path=f_mp3,
                                title=t_title,
                                artist=t_artist,
                                album=t_album,
                                year=t_year,
                                track_num=idx,
                                lyrics=lyrics_text,
                                cover_url=t_cover
                            )
                            downloaded_count += 1
                    except Exception as err:
                        logger.warning(f"Failed to download track {idx} ({t_title}): {err}")

                t["progress"] = 100.0
                t["filepath"] = str(target_dir)
                t["filename"] = target_dir.name
                if downloaded_count == 0 and total_tracks > 0:
                    t["status"] = "❌ هیچ قطعه‌ای دانلود نشد."
                    t["error"] = "خطا در دانلود قطعات یا لغو عملیات."
                else:
                    t["status"] = f"✅ تعداد {downloaded_count} از {total_tracks} قطعه با موفقیت ذخیره شدند!"
                t["is_done"] = True

            # -------------------------------------------------------------
            # YOUTUBE: Playlist / Batch Download (Video or Audio)
            # -------------------------------------------------------------
            elif action_type == "youtube_batch":
                playlist_title = extra.get("playlist_title") or "YouTube Playlist"
                format_type = extra.get("format_type") or "video"
                items = extra.get("items") or []

                if not items:
                    t["status"] = "در حال استخراج لیست ویدیوهای پلی‌لیست..."
                    pl_info = await self.youtube_service.get_playlist_info(url)
                    items = pl_info.get("videos", [])
                    playlist_title = pl_info.get("title", playlist_title)

                total_items = len(items)
                safe_folder = sanitize_filename(f"YouTube - {playlist_title}")
                target_dir = settings.download_dir / safe_folder
                target_dir.mkdir(parents=True, exist_ok=True)

                lbl_type = "ویدیو" if format_type == "video" else "فایل صوتی MP3"
                t["title"] = f"پلی‌لیست یوتیوب ({'ویدیو' if format_type == 'video' else 'صوت'}): {playlist_title}"
                t["total"] = f"{total_items} {lbl_type}"

                # Delta Sync: identify items already downloaded in this folder
                existing_ids = _get_existing_video_ids(target_dir)
                new_items = [item for item in items if not (item.get("id") and item.get("id") in existing_ids)]
                skipped_count = total_items - len(new_items)

                if skipped_count > 0:
                    logger.info(f"Playlist delta sync: {len(new_items)} new, {skipped_count} already downloaded.")
                    t["status"] = f"🔍 {len(new_items)} آیتم جدید | {skipped_count} مورد از قبل موجود"

                if not new_items:
                    t["progress"] = 100.0
                    t["filepath"] = str(target_dir)
                    t["filename"] = target_dir.name
                    t["status"] = f"✅ تمام {total_items} {lbl_type} از قبل در سیستم موجود است."
                    t["is_done"] = True
                    return

                downloaded_count = 0
                ratelimit_kbps = extra.get("ratelimit_kbps", 0)

                for idx, item in enumerate(new_items, 1):
                    if t.get("is_cancelled") or (t.get("error") and "لغو" in t.get("error", "")):
                        logger.info(f"YouTube batch task {task_id} aborted by user.")
                        break

                    v_id = item.get("id")
                    v_title = item.get("title") or f"Video {idx}"
                    v_channel = item.get("channel") or "YouTube"
                    v_thumb = item.get("thumbnail")
                    v_url = item.get("url") or f"https://www.youtube.com/watch?v={v_id}"

                    safe_title = sanitize_filename(f"{idx:02d} - {v_title} [{v_id}]" if v_id else f"{idx:02d} - {v_title}")
                    t["status"] = f"در حال دانلود {lbl_type} {idx} از {len(new_items)} (مجموع {total_items}): {v_title}"
                    t["progress"] = round(((idx - 1) / len(new_items)) * 100, 1)

                    try:
                        if format_type == "video":
                            out_tmpl = str(target_dir / f"{safe_title}_{quality}p.%(ext)s")

                            def _down_pl_vid():
                                opts = self._get_common_ydl_opts(out_tmpl, None, ratelimit_kbps=ratelimit_kbps)
                                opts.update({
                                    'format': (
                                        f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
                                    ),
                                    'merge_output_format': 'mp4',
                                    'ignoreerrors': True,
                                })
                                with yt_dlp.YoutubeDL(opts) as ydl:
                                    ydl.download([v_url])

                            await asyncio.to_thread(_down_pl_vid)

                            expected_mp4 = target_dir / f"{safe_title}_{quality}p.mp4"
                            if not expected_mp4.exists():
                                matches = list(target_dir.glob(f"{safe_title}_{quality}p*.mp4"))
                                if matches:
                                    expected_mp4 = matches[0]

                            if expected_mp4.exists() and expected_mp4.stat().st_size > 0:
                                downloaded_count += 1
                                # Auto-save companion transcripts
                                await self._save_companion_transcripts(v_url, v_id or "", expected_mp4)

                        else:  # "audio"
                            out_tmpl = str(target_dir / f"{safe_title}.%(ext)s")
                            is_opus = (quality == "opus")

                            def _down_pl_aud():
                                opts = self._get_common_ydl_opts(out_tmpl, None, ratelimit_kbps=ratelimit_kbps)
                                if is_opus:
                                    opts.update({
                                        'format': 'bestaudio[ext=opus]/bestaudio/best',
                                        'ignoreerrors': True,
                                        'postprocessors': [{
                                            'key': 'FFmpegExtractAudio',
                                            'preferredcodec': 'opus',
                                        }],
                                    })
                                else:
                                    opts.update({
                                        'format': 'bestaudio/best',
                                        'ignoreerrors': True,
                                        'postprocessors': [{
                                            'key': 'FFmpegExtractAudio',
                                            'preferredcodec': 'mp3',
                                            'preferredquality': '192',
                                        }],
                                    })
                                with yt_dlp.YoutubeDL(opts) as ydl:
                                    ydl.download([v_url])

                            await asyncio.to_thread(_down_pl_aud)

                            ext_target = "opus" if is_opus else "mp3"
                            expected_file = target_dir / f"{safe_title}.{ext_target}"
                            if not expected_file.exists():
                                matches = list(target_dir.glob(f"{safe_title}*.{ext_target}"))
                                if matches:
                                    expected_file = matches[0]

                            if expected_file.exists() and expected_file.stat().st_size > 0:
                                if is_opus:
                                    await self.media_service.tag_opus(
                                        opus_path=expected_file,
                                        title=v_title,
                                        artist=v_channel,
                                        album=playlist_title,
                                        track_num=idx,
                                        cover_url=v_thumb
                                    )
                                else:
                                    await self.media_service.tag_mp3(
                                        mp3_path=expected_file,
                                        title=v_title,
                                        artist=v_channel,
                                        album=playlist_title,
                                        track_num=idx,
                                        cover_url=v_thumb
                                    )
                                downloaded_count += 1

                    except Exception as err:
                        logger.warning(f"Error downloading playlist item {idx} ({v_title}): {err}")

                t["progress"] = 100.0
                t["filepath"] = str(target_dir)
                t["filename"] = target_dir.name
                if downloaded_count == 0 and skipped_count == 0 and total_items > 0:
                    t["status"] = f"❌ هیچ موردی از {total_items} {lbl_type} دانلود نشد."
                    t["error"] = "خطا در دانلود تمامی موارد یا لغو عملیات."
                else:
                    t["status"] = f"✅ تعداد {downloaded_count} جدید ({skipped_count} از قبل موجود) با موفقیت در پوشه ذخیره شدند!"
                t["is_done"] = True

            # -------------------------------------------------------------
            # YOUTUBE: Video Download
            # -------------------------------------------------------------
            elif action_type == "video":
                info = await self.youtube_service.get_info(url)
                t["title"] = info.get('title', 'ویدیو یوتیوب')
                safe_title = sanitize_filename(info.get('title', 'video'))
                t["status"] = f"در حال دانلود ویدیو ({quality}p)..."
                out_tmpl = str(settings.download_dir / f"{safe_title}_{quality}p.%(ext)s")

                def _download_vid():
                    opts = self._get_common_ydl_opts(out_tmpl, hook)
                    opts.update({
                        'format': (
                            f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
                        ),
                        'merge_output_format': 'mp4',
                    })
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([url])

                await asyncio.to_thread(_download_vid)

                expected = Path(out_tmpl.replace('%(ext)s', 'mp4'))
                if not expected.exists():
                    matches = [
                        f for f in settings.download_dir.glob(f"{safe_title}_{quality}p*.*")
                        if not f.name.endswith(('.part', '.ytdl', '.temp', '.aria2'))
                    ]
                    expected = matches[0] if matches else None

                if expected and expected.exists():
                    # Auto-fetch companion transcripts (English & Persian) for synchronized playback
                    await self._save_companion_transcripts(url, info.get('video_id', ''), expected)

                    t["filepath"] = str(expected)
                    t["filename"] = expected.name
                    t["total"] = format_bytes(expected.stat().st_size)
                    t["progress"] = 100.0
                    t["status"] = "✅ دانلود ویدیو با موفقیت تکمیل شد!"
                    t["is_done"] = True
                else:
                    raise FileNotFoundError("ویدیو پس از دانلود پیدا نشد.")

            # -------------------------------------------------------------
            # YOUTUBE: Audio Download
            # -------------------------------------------------------------
            elif action_type == "audio":
                info = await self.youtube_service.get_info(url)
                is_opus = (quality == "opus")
                fmt_label = "Opus (بومی)" if is_opus else "MP3"
                t["title"] = f"صوت ({fmt_label}): {info.get('title', 'یوتیوب')}"
                safe_title = sanitize_filename(info.get('title', 'video'))
                t["status"] = f"در حال استخراج صوت ({fmt_label})..."
                out_tmpl = str(settings.download_dir / f"{safe_title}.%(ext)s")

                def _download_aud():
                    opts = self._get_common_ydl_opts(out_tmpl, hook)
                    if is_opus:
                        opts.update({
                            'format': 'bestaudio[ext=opus]/bestaudio/best',
                            'postprocessors': [{
                                'key': 'FFmpegExtractAudio',
                                'preferredcodec': 'opus',
                            }],
                        })
                    else:
                        opts.update({
                            'format': 'bestaudio/best',
                            'postprocessors': [{
                                'key': 'FFmpegExtractAudio',
                                'preferredcodec': 'mp3',
                                'preferredquality': '192',
                            }],
                        })
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([url])

                await asyncio.to_thread(_download_aud)

                ext_target = "opus" if is_opus else "mp3"
                audio_file = Path(out_tmpl.replace('%(ext)s', ext_target))
                if not audio_file.exists():
                    matches = list(settings.download_dir.glob(f"{safe_title}*.{ext_target}"))
                    audio_file = matches[0] if matches else None

                if audio_file and audio_file.exists():
                    if is_opus:
                        await self.media_service.tag_opus(
                            opus_path=audio_file,
                            title=info.get('title', 'Audio'),
                            artist=info.get('channel', 'YouTube'),
                            cover_url=info.get('thumbnail')
                        )
                    else:
                        await self.media_service.tag_mp3(
                            mp3_path=audio_file,
                            title=info.get('title', 'Audio'),
                            artist=info.get('channel', 'YouTube'),
                            cover_url=info.get('thumbnail')
                        )

                    # Auto-fetch companion transcripts (English & Persian) for synchronized podcast/playback
                    await self._save_companion_transcripts(url, info.get('video_id', ''), audio_file)

                    t["filepath"] = str(audio_file)
                    t["filename"] = audio_file.name
                    t["total"] = format_bytes(audio_file.stat().st_size)
                    t["progress"] = 100.0
                    t["status"] = f"✅ فایل {fmt_label} با کیفیت عالی آماده شد!"
                    t["is_done"] = True
                else:
                    raise FileNotFoundError(f"فایل صوتی {fmt_label} یافت نشد.")

            # -------------------------------------------------------------
            # YOUTUBE: Subtitle Download
            # -------------------------------------------------------------
            elif action_type == "subtitle":
                info = await self.youtube_service.get_info(url)
                t["title"] = f"زیرنویس: {info.get('title', 'یوتیوب')}"
                safe_title = sanitize_filename(info.get('title', 'video'))
                lang_label = "فارسی" if lang == "fa" else "انگلیسی"
                t["status"] = f"در حال استخراج زیرنویس {lang_label}..."
                report_file, _ = await self.subtitle_service.get_transcript_report(url, lang, info)

                dest_file = settings.download_dir / f"{safe_title}_{lang}.txt"
                dest_file.write_text(report_file.read_text(encoding="utf-8"), encoding="utf-8")

                t["filepath"] = str(dest_file)
                t["filename"] = dest_file.name
                t["total"] = format_bytes(dest_file.stat().st_size)
                t["progress"] = 100.0
                t["status"] = f"✅ متن زیرنویس ({lang_label}) آماده شد!"
                t["is_done"] = True

        except (asyncio.CancelledError, yt_dlp.utils.DownloadCancelled):
            logger.info(f"Task {task_id} was cancelled by user.")
            t["status"] = "🛑 دانلود متوقف شد"
            t["error"] = "دانلود توسط کاربر متوقف شد."
            t["is_done"] = True
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            t["status"] = "❌ خطا در دانلود"
            t["error"] = str(e)
            t["is_done"] = True


task_manager = DownloadTaskManager()