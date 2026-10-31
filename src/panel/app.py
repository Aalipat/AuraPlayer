"""
FastAPI application for local YouTube & Spotify Downloader Desktop Panel.
Supports YouTube video/audio/subtitles and Spotify track/album/lyrics downloads.
"""
import asyncio
import logging
import os
import re
import subprocess
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mutagen.id3 import ID3
from mutagen.oggopus import OggOpus
from pydantic import BaseModel

from src.bot.config import BASE_DIR, settings
from src.panel.tasks import task_manager
from src.services.lyrics import lyrics_service
from src.services.media import MediaService, ThumbnailService, WaveformService
from src.services.scheduler import scheduler_service
from src.services.spotify import is_valid_spotify_url, spotify_service
from src.services.subtitles import SubtitleService
from src.services.youtube import (
    YouTubeService,
    extract_playlist_id,
    is_valid_youtube_playlist_url,
    is_valid_youtube_url,
)
from src.utils.formatting import format_bytes, format_seconds


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start scheduler loop in background
    sched_task = asyncio.create_task(scheduler_service.start_background_loop(task_manager))
    yield
    # Shutdown
    scheduler_service.stop()
    sched_task.cancel()


app = FastAPI(title="Local YouTube & Spotify Downloader Panel", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

youtube_service = YouTubeService()
subtitle_service = SubtitleService()
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    type: str  # "video", "audio", "subtitle", "spotify_audio", "spotify_lyrics", "spotify_batch", "youtube_batch"
    quality: Optional[str] = "720"
    lang: Optional[str] = "fa"
    extra: Optional[Dict[str, Any]] = None


class SubtitlePreviewRequest(BaseModel):
    url: str
    lang: str = "fa"


class SpotifyLyricsRequest(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    duration: Optional[int] = 0


class QueueAddRequest(BaseModel):
    url: str
    type: str  # "video", "audio", "spotify_audio", etc.
    quality: Optional[str] = "720"
    lang: Optional[str] = "fa"
    title: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class QueueRemoveRequest(BaseModel):
    id: str


class QueueConfigRequest(BaseModel):
    schedule_enabled: Optional[bool] = None
    schedule_start: Optional[str] = None
    schedule_end: Optional[str] = None
    bandwidth_limit_kbps: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.post("/api/info")
async def get_media_info(req: InfoRequest):
    url = req.url.strip()

    # 1. Spotify Detection
    if is_valid_spotify_url(url):
        try:
            sp_meta = await spotify_service.get_metadata(url)
            return {
                "success": True,
                "platform": "spotify",
                **sp_meta
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"خطا در دریافت اطلاعات اسپاتیفای: {str(e)}")

    # 2. YouTube Playlist Detection
    elif is_valid_youtube_playlist_url(url):
        try:
            pl_info = await youtube_service.get_playlist_info(url)
            return {
                "success": True,
                "platform": "youtube",
                "entity_type": "playlist",
                **pl_info
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"خطا در دریافت اطلاعات پلی‌لیست یوتیوب: {str(e)}")

    # 3. YouTube Single Video Detection
    elif is_valid_youtube_url(url):
        try:
            info = await youtube_service.get_info(url)
            return {
                "success": True,
                "platform": "youtube",
                "entity_type": "video",
                "title": info["title"],
                "channel": info["channel"],
                "duration": format_seconds(info["duration"]),
                "duration_seconds": info["duration"],
                "thumbnail": info.get("thumbnail"),
                "video_id": info["video_id"],
                "playlist_id": info.get("playlist_id"),
                "qualities": {
                    k: format_bytes(v) if v else None
                    for k, v in info.get("qualities", {}).items()
                },
                "has_fa_sub": info.get("has_fa_sub", False),
                "has_en_sub": info.get("has_en_sub", False),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"خطا در دریافت اطلاعات یوتیوب: {str(e)}")

    raise HTTPException(
        status_code=400,
        detail="لینک وارد شده معتبر نیست. لطفاً یک آدرس معتبر از یوتیوب یا اسپاتیفای (Track / Album / Playlist) وارد کنید."
    )


@app.post("/api/download")
async def start_download(req: DownloadRequest):
    url = req.url.strip()
    is_yt = is_valid_youtube_url(url)
    is_sp = is_valid_spotify_url(url)

    if not is_yt and not is_sp:
        raise HTTPException(status_code=400, detail="لینک معتبر یوتیوب یا اسپاتیفای نیست.")

    settings.ensure_directories()
    task_id = task_manager.create_task(
        url=url,
        action_type=req.type,
        quality=req.quality or ("320" if is_sp else "720"),
        lang=req.lang or "fa",
        extra=req.extra
    )
    async_task = asyncio.create_task(task_manager.execute_task(task_id))
    task_manager.register_async_task(task_id, async_task)
    return {"success": True, "task_id": task_id}


@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="تسک یافت نشد.")
    return task


@app.get("/api/tasks/active")
async def get_active_tasks():
    return {"tasks": task_manager.get_all_tasks(include_done=True, limit=20)}


@app.post("/api/tasks/cancel/{task_id}")
async def cancel_task(task_id: str):
    cancelled = task_manager.cancel_task(task_id)
    return {"success": cancelled}


@app.post("/api/tasks/dismiss/{task_id}")
async def dismiss_task(task_id: str):
    removed = task_manager.dismiss_task(task_id)
    return {"success": removed}


@app.post("/api/tasks/clear-completed")
async def clear_completed_tasks():
    tasks = task_manager.get_all_tasks(include_done=True, limit=50)
    count = 0
    for t in tasks:
        if t.get("is_done"):
            task_manager.dismiss_task(t["id"])
            count += 1
    return {"success": True, "cleared": count}


@app.get("/api/queue")
async def get_queue():
    return scheduler_service.load_config()


@app.post("/api/queue/add")
async def add_to_queue(req: QueueAddRequest):
    item = scheduler_service.add_item(
        url=req.url,
        type_=req.type,
        quality=req.quality,
        lang=req.lang,
        title=req.title,
        extra=req.extra,
    )
    return {"success": True, "item": item}


@app.post("/api/queue/remove")
async def remove_from_queue(req: QueueRemoveRequest):
    removed = scheduler_service.remove_item(req.id)
    return {"success": removed}


@app.post("/api/queue/config")
async def update_queue_config(req: QueueConfigRequest):
    cfg = scheduler_service.update_config(
        enabled=req.schedule_enabled,
        start=req.schedule_start,
        end=req.schedule_end,
        bandwidth_limit_kbps=req.bandwidth_limit_kbps,
    )
    return {"success": True, "config": cfg}


@app.post("/api/queue/start-now")
async def start_queue_item_now(req: QueueRemoveRequest):
    started = await scheduler_service.force_start_item(req.id, task_manager)
    return {"success": started}


@app.post("/api/queue/start-all")
async def start_all_queue_items_now():
    count = await scheduler_service.force_start_all(task_manager)
    return {"success": True, "started_count": count}


@app.post("/api/queue/clear-completed")
async def clear_completed_queue():
    cleared = scheduler_service.clear_completed()
    return {"success": True, "cleared": cleared}


@app.post("/api/spotify/lyrics")
async def get_spotify_lyrics(req: SpotifyLyricsRequest):
    title = req.title or ""
    artist = req.artist or ""
    duration = req.duration or 0

    if req.url and (not title or not artist):
        try:
            if "spotify.com" in req.url:
                meta = await spotify_service.get_metadata(req.url)
                title = meta.get("title", "")
                artist = meta.get("artist", "")
                duration = meta.get("duration_seconds", 0)
        except Exception as e:
            logger.debug(f"Spotify metadata fetch error in lyrics endpoint: {e}")

    try:
        lyrics_data = await lyrics_service.get_lyrics(
            media_path=None,
            title=title,
            artist=artist,
            duration=duration,
            include_translation=True
        )
        if not lyrics_data.get("has_lyrics"):
            return {
                "success": False,
                "has_lyrics": False,
                "message": "متن ترانه در دیتابیس لیریکس یافت نشد."
            }

        return lyrics_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطا در واکشی متن ترانه: {e}")


class OpenFolderRequest(BaseModel):
    path: Optional[str] = None


def _sync_open_explorer(target_path: Optional[Path], folder: Path) -> dict:
    try:
        if target_path and target_path.is_file():
            # Open Windows Explorer with the specific file selected
            subprocess.Popen(["explorer.exe", f"/select,{str(target_path)}"])
            return {"success": True, "path": str(target_path), "is_file": True}
        else:
            dest = target_path if (target_path and target_path.is_dir()) else folder
            subprocess.Popen(["explorer.exe", str(dest)])
            return {"success": True, "path": str(dest), "is_file": False}
    except Exception as err:
        return {"success": False, "error": str(err), "path": str(target_path or folder)}


@app.post("/api/open-folder")
async def open_download_folder(req: Optional[OpenFolderRequest] = None):
    folder = settings.download_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target_path = None
    if req and req.path:
        p = Path(req.path).resolve()
        if p.exists():
            target_path = p

    res = await asyncio.to_thread(_sync_open_explorer, target_path, folder)
    if not res.get("success", False) and "error" in res:
        raise HTTPException(status_code=500, detail=f"عدم امکان باز کردن پوشه: {res['error']}")
    return res


IGNORE_DOWNLOAD_EXTENSIONS = {
    ".part", ".ytdl", ".temp", ".aria2",
    ".jpg", ".jpeg", ".png", ".webp",
    ".lrc", ".txt", ".gitkeep"
}

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")

MEDIA_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
}


def stream_file_chunks(path: Path, start: int, end: int, chunk_size: int = 512 * 1024) -> Generator[bytes, None, None]:
    """Generates file slices in 512KB chunks for optimal memory & seek responsiveness."""
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            read_bytes = min(chunk_size, remaining)
            data = f.read(read_bytes)
            if not data:
                break
            remaining -= len(data)
            yield data


def _sync_list_downloads(folder: Path) -> list:
    files = []
    try:
        with os.scandir(str(folder)) as entries:
            for entry in entries:
                if entry.is_file():
                    p = Path(entry.path)
                    ext = p.suffix.lower()
                    if ext in IGNORE_DOWNLOAD_EXTENSIONS or entry.name.startswith("."):
                        continue

                    stat = entry.stat()
                    artist = ""
                    title = p.stem.replace("_", " ")

                    # Extract ID3 title & artist for clean display
                    if ext == ".mp3":
                        try:
                            audio = ID3(entry.path)
                            if "TIT2" in audio:
                                title = str(audio["TIT2"])
                            if "TPE1" in audio:
                                artist = str(audio["TPE1"])
                        except Exception:
                            pass
                    elif ext in (".opus", ".ogg"):
                        try:
                            opus_tags = OggOpus(entry.path)
                            if "title" in opus_tags and opus_tags["title"]:
                                title = str(opus_tags["title"][0])
                            if "artist" in opus_tags and opus_tags["artist"]:
                                artist = str(opus_tags["artist"][0])
                        except Exception:
                            pass

                    encoded_path = urllib.parse.quote(entry.path)
                    files.append({
                        "name": entry.name,
                        "title": title,
                        "artist": artist,
                        "size": format_bytes(stat.st_size),
                        "path": entry.path,
                        "modified": stat.st_mtime,
                        "ext": ext.replace(".", ""),
                        "cover_url": f"/api/media/thumbnail?path={encoded_path}",
                    })
    except Exception:
        return []

    # Sort descending by modified date and limit to recent 50
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files[:50]


@app.get("/api/downloads")
async def list_downloads():
    folder = settings.download_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    files = await asyncio.to_thread(_sync_list_downloads, folder)
    return {"files": files}


@app.get("/api/media/thumbnail")
async def get_media_thumbnail(path: str):
    target = Path(path).resolve()
    base_folder = settings.download_dir.resolve()
    if not target.is_relative_to(base_folder):
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز به فایل.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="فایل یافت نشد.")

    thumb = await ThumbnailService.get_or_create_thumbnail(target)
    if not thumb or not thumb.exists():
        raise HTTPException(status_code=404, detail="کاور برای این فایل یافت نشد.")

    ext = thumb.suffix.lower()
    mime = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
    headers = {
        "Cache-Control": "public, max-age=604800, immutable",
        "ETag": f'"{thumb.stat().st_mtime}-{thumb.stat().st_size}"'
    }
    return FileResponse(thumb, media_type=mime, headers=headers)


@app.get("/api/media/waveform")
async def get_media_waveform(path: str, samples: int = 120):
    target = Path(path).resolve()
    base_folder = settings.download_dir.resolve()
    if not target.is_relative_to(base_folder):
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز به فایل.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="فایل مدیا یافت نشد.")

    samples = max(20, min(300, int(samples)))
    peaks = await WaveformService.get_or_create_waveform(target, samples=samples)
    return {"peaks": peaks, "samples": len(peaks)}


@app.get("/api/media/lyrics")
async def get_media_lyrics(
    path: Optional[str] = None,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    duration: Optional[float] = 0,
):
    target = None
    if path:
        p = Path(path).resolve()
        base_folder = settings.download_dir.resolve()
        if p.is_relative_to(base_folder) and p.exists() and p.is_file():
            target = p

    clean_title = (title or "").strip()
    clean_artist = (artist or "").strip()
    if not clean_title and target:
        clean_title = target.stem

    res = await lyrics_service.get_lyrics(
        media_path=target,
        title=clean_title,
        artist=clean_artist,
        album=album or "",
        duration=duration or 0,
    )
    return res


class DeleteDownloadRequest(BaseModel):
    path: str


@app.post("/api/downloads/delete")
async def delete_downloaded_file(req: DeleteDownloadRequest):
    target = Path(req.path).resolve()
    base_folder = settings.download_dir.resolve()
    if not target.is_relative_to(base_folder):
        raise HTTPException(status_code=403, detail="دسترسی به فایل خارج از پوشه دانلود مجاز نیست.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="فایل پیدا نشد.")
    try:
        target.unlink()
        return {"success": True, "message": "فایل با موفقیت حذف شد."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطا در حذف فایل: {e}")


@app.get("/api/media/stream")
@app.head("/api/media/stream")
async def stream_media(path: str, request: Request):
    target = Path(path).resolve()
    base_folder = settings.download_dir.resolve()
    if not target.is_relative_to(base_folder):
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز به فایل.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="فایل مدیا یافت نشد.")

    file_size = target.stat().st_size
    ext = target.suffix.lower()
    media_type = MEDIA_MIME_TYPES.get(ext, "application/octet-stream")
    range_header = request.headers.get("Range")

    # If HEAD request: return metadata headers only
    if request.method == "HEAD":
        return Response(
            status_code=status.HTTP_200_OK,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Type": media_type,
            }
        )

    # 1. Standard streaming when no Range header is sent
    if not range_header:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": media_type,
            "Cache-Control": "public, max-age=86400",
        }
        return StreamingResponse(
            stream_file_chunks(target, 0, file_size - 1),
            status_code=status.HTTP_200_OK,
            headers=headers
        )

    # 2. Parse HTTP Range header (e.g., 'bytes=1048576-' or 'bytes=0-1048575')
    match = RANGE_PATTERN.match(range_header.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    start_str, end_str = match.groups()
    if start_str and end_str:
        start = int(start_str)
        end = int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    elif end_str:
        start = max(file_size - int(end_str), 0)
        end = file_size - 1
    else:
        start = 0
        end = file_size - 1

    if start > end or start >= file_size or end >= file_size:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": media_type,
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(
        stream_file_chunks(target, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers
    )


@app.post("/api/subtitles/preview")
async def preview_subtitles(req: SubtitlePreviewRequest):
    url = req.url.strip()
    try:
        info = await youtube_service.get_info(url)
        report_file, preview = await subtitle_service.get_transcript_report(url, req.lang, info)
        content = report_file.read_text(encoding="utf-8")
        return {"success": True, "content": content, "filename": report_file.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ProxyConfigRequest(BaseModel):
    mode: str  # "auto", "direct", or custom URL


@app.get("/api/proxy-status")
async def get_proxy_status():
    active = settings.get_active_proxy()
    return {
        "configured": settings.proxy_url,
        "active": active,
        "mode_label": "اتصال مستقیم (بدون پروکسی)" if not active else f"پروکسی فعال: {active}",
        "is_direct": active is None
    }


@app.post("/api/proxy-config")
async def set_proxy_config(req: ProxyConfigRequest):
    mode = req.mode.strip()
    settings.proxy_url = mode
    settings.clear_proxy_cache()
    active = settings.get_active_proxy(force_refresh=True)
    return {
        "success": True,
        "configured": settings.proxy_url,
        "active": active,
        "mode_label": "اتصال مستقیم (بدون پروکسی)" if not active else f"پروکسی فعال: {active}",
        "is_direct": active is None
    }


class FolderConfigRequest(BaseModel):
    path: str


def _pick_folder_dialog(initial_dir: str) -> Optional[str]:
    safe_init = initial_dir.replace("'", "''") if initial_dir else ""
    ps_code = f"""
    [System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null
    $f = New-Object System.Windows.Forms.FolderBrowserDialog
    $f.Description = 'پوشه دلخواه جهت ذخیره دانلودها را انتخاب کنید'
    $f.UseDescriptionForTitle = $true
    $f.ShowNewFolderButton = $true
    if ('{safe_init}' -and (Test-Path '{safe_init}')) {{ $f.SelectedPath = '{safe_init}' }}
    if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Write-Output $f.SelectedPath
    }}
    """
    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            startupinfo=startupinfo,
            timeout=120
        )
        if proc.returncode == 0:
            selected = proc.stdout.strip()
            if selected and Path(selected).exists():
                return selected
    except Exception as e:
        logger.warning(f"Native folder picker error: {e}")
    return None


@app.get("/api/folder-config")
async def get_folder_config():
    default_dir = (BASE_DIR / "data" / "downloads").resolve()
    current_dir = settings.download_dir.resolve()
    return {
        "current_path": str(current_dir),
        "default_path": str(default_dir),
        "is_custom": current_dir != default_dir,
    }


@app.post("/api/set-folder")
async def set_folder_config(req: FolderConfigRequest):
    p = req.path.strip()
    if not p:
        raise HTTPException(status_code=400, detail="مسیر پوشه نمی‌تواند خالی باشد.")
    try:
        resolved = settings.set_download_dir(p)
        return {
            "success": True,
            "current_path": str(resolved),
            "message": f"مسیر ذخیره با موفقیت تنظیم شد: {resolved}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطا در تنظیم مسیر پوشه: {e}")


@app.post("/api/select-folder-dialog")
async def select_folder_dialog():
    curr = str(settings.download_dir.resolve())
    picked = await asyncio.to_thread(_pick_folder_dialog, curr)
    if picked:
        resolved = settings.set_download_dir(picked)
        return {
            "success": True,
            "selected": True,
            "current_path": str(resolved),
        }
    return {
        "success": True,
        "selected": False,
        "current_path": curr,
    }


@app.post("/api/reset-folder")
async def reset_folder():
    default_dir = (BASE_DIR / "data" / "downloads").resolve()
    resolved = settings.set_download_dir(str(default_dir))
    return {
        "success": True,
        "current_path": str(resolved),
        "message": "مسیر ذخیره به پوشه پیش‌فرض بازنشانی شد."
    }


@app.get("/api/system/info")
async def get_system_info():
    import yt_dlp
    return {
        "app_version": "2.0.0-Desktop",
        "yt_dlp_version": yt_dlp.version.__version__,
        "proxy": settings.get_active_proxy() or "مستقیم (Direct)",
        "download_dir": str(settings.download_dir.resolve()),
        "ffmpeg_path": settings.ffmpeg_path,
    }


@app.post("/api/system/update-ytdlp")
async def update_ytdlp():
    """Checks for latest yt-dlp and updates it directly inside runtime."""
    import yt_dlp
    import httpx

    current_ver = yt_dlp.version.__version__
    proxy = settings.get_active_proxy()
    transport = httpx.AsyncHTTPTransport(proxy=proxy) if proxy else None

    try:
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
            res = await client.get("https://pypi.org/pypi/yt-dlp/json")
            if res.status_code != 200:
                raise HTTPException(status_code=500, detail="امکان اتصال به مخزن PyPI وجود ندارد.")
            data = res.json()
            latest_ver = data["info"]["version"]

        if latest_ver == current_ver:
            return {
                "success": True,
                "updated": False,
                "current_version": current_ver,
                "message": f"شما در حال حاضر از جدیدترین نسخه yt-dlp ({current_ver}) استفاده می‌کنید."
            }

        # Run pip install --upgrade yt-dlp using local runtime python
        py_exe = BASE_DIR / "runtime" / "python.exe"
        cmd = [str(py_exe), "-m", "pip", "install", "--upgrade", "yt-dlp", "--no-warn-script-location"]
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"خطا در ارتقای yt-dlp: {proc.stderr}")

        return {
            "success": True,
            "updated": True,
            "old_version": current_ver,
            "new_version": latest_ver,
            "message": f"yt-dlp با موفقیت به نسخه {latest_ver} ارتقا یافت!"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"خطا در فرآیند بررسی آپدیت: {e}")


