"""
Subtitles and transcript extraction, clean parsing, AI translation, and report generation.
Supports auto-generated captions and automatic translation to Persian/English.
"""
import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from src.bot.config import settings
from src.utils.formatting import format_seconds

import httpx

logger = logging.getLogger(__name__)


def clean_vtt_text(vtt_content: str) -> str:
    """Strip VTT/SRT headers, timestamps, formatting tags, and duplicate lines."""
    lines = vtt_content.splitlines()
    cleaned = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if "-->" in line:
            continue
        line = re.sub(r'<[^>]+>', '', line).strip()
        if not line or line.isdigit():
            continue
        if cleaned and cleaned[-1] == line:
            continue
        cleaned.append(line)

    paragraphs = []
    current_para = []
    for line in cleaned:
        current_para.append(line)
        if len(current_para) >= 5 or line.endswith(('.', '!', '?', '؛', '؟')):
            paragraphs.append(" ".join(current_para))
            current_para = []
    if current_para:
        paragraphs.append(" ".join(current_para))

    return "\n\n".join(paragraphs)


def _translate_snippets(snippets: list, target_lang: str = "fa", proxy_url: Optional[str] = None) -> list[str]:
    """Batch-translates transcript snippets to target_lang while preserving timestamps with proxy support."""
    results = []
    chunk = []
    chunk_len = 0

    def _flush_chunk(chk):
        if not chk:
            return
        combined = "\n".join([x.text for x in chk])
        try:
            import requests
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "dt": "t",
                "q": combined,
            }
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            resp = requests.get(url, params=params, headers=headers, proxies=proxies, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                trans_full = ''.join([item[0] for item in data[0] if item and item[0]])
                trans_lines = trans_full.splitlines()
                for i, s in enumerate(chk):
                    t_text = trans_lines[i] if i < len(trans_lines) else s.text
                    results.append(f"[{format_seconds(s.start)}] {t_text}")
            else:
                for s in chk:
                    results.append(f"[{format_seconds(s.start)}] {s.text}")
        except Exception as e:
            logger.warning(f"Batch translation failed, falling back to original: {e}")
            for s in chk:
                results.append(f"[{format_seconds(s.start)}] {s.text}")

    for s in snippets:
        chunk.append(s)
        chunk_len += len(s.text)
        if chunk_len >= 1500:
            _flush_chunk(chunk)
            chunk = []
            chunk_len = 0

    if chunk:
        _flush_chunk(chunk)

    return results


class SubtitleService:
    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url

    def _sync_fetch_transcript(self, url: str, lang: str, video_id: str) -> Tuple[str, str]:
        """
        Fetch transcript with intelligent dual-strategy and auto-translation:
        1. Direct match in requested language (official or auto-captions).
        2. YouTube native translation if available.
        3. Automatic on-the-fly translation to target language (e.g. English -> Persian).
        4. Fallback: yt-dlp native extraction.
        Returns (cleaned_text, source_type).
        """
        active_proxy = self.proxy_url or settings.get_active_proxy()

        # --- Strategy 1: youtube-transcript-api ---
        try:
            proxy_config = None
            if active_proxy:
                try:
                    from youtube_transcript_api.proxies import GenericProxyConfig
                    proxy_config = GenericProxyConfig(http_url=active_proxy, https_url=active_proxy)
                except Exception as p_err:
                    logger.debug(f"Could not configure GenericProxyConfig: {p_err}")

            ytt = YouTubeTranscriptApi(proxy_config=proxy_config)
            transcript_list = ytt.list(video_id)

            # 1. Exact language match
            target = next((t for t in transcript_list if t.language_code == lang), None)

            # 2. YouTube native translation
            if not target:
                for t in transcript_list:
                    if t.is_translatable:
                        match_lang = next((x for x in t.translation_languages if x.language_code == lang), None)
                        if match_lang:
                            target = t.translate(lang)
                            break

            if target:
                snippets = target.fetch()
                lines = []
                for s in snippets:
                    ts = format_seconds(s.start)
                    lines.append(f"[{ts}] {s.text}")
                source_type = "official" if not target.is_generated else "automatic"
                return "\n".join(lines), source_type

            # 3. Intelligent auto-translation from available base transcript (e.g. English speech -> Persian)
            base_t = next((t for t in transcript_list if t.language_code == 'en'), None)
            if not base_t and transcript_list:
                base_t = list(transcript_list)[0]

            if base_t:
                logger.info(f"Translating transcript from {base_t.language_code} to {lang} for {video_id}...")
                snippets = base_t.fetch()
                translated_lines = _translate_snippets(snippets, target_lang=lang, proxy_url=active_proxy)
                if translated_lines:
                    return "\n".join(translated_lines), "auto_translated"

        except Exception as e:
            logger.warning(f"youtube-transcript-api attempt failed for {video_id}: {e}")

        # --- Strategy 4: yt-dlp fallback ---
        try:
            temp_base = settings.transcript_dir / f"raw_{video_id}_{lang}"
            opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': [lang],
                'subtitlesformat': 'vtt',
                'outtmpl': str(temp_base) + '.%(ext)s',
                'ffmpeg_location': settings.ffmpeg_dir,
                'nocheckcertificate': True,
                'socket_timeout': 30,
            }
            active_proxy = self.proxy_url or settings.get_active_proxy()
            if active_proxy:
                opts['proxy'] = active_proxy

            if settings.node_path and Path(settings.node_path).exists():
                opts['js_runtimes'] = {'node': {'path': str(settings.node_path)}}

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                subtitles = info.get('subtitles', {}) or {}
                auto_captions = info.get('automatic_captions', {}) or {}

                source_type = "none"
                if lang in subtitles and subtitles[lang]:
                    source_type = "official"
                elif lang in auto_captions and auto_captions[lang]:
                    source_type = "automatic"

                if source_type != "none":
                    ydl.download([url])
                    matches = list(settings.transcript_dir.glob(f"raw_{video_id}_{lang}*.vtt"))
                    if matches:
                        raw_vtt = matches[0].read_text(encoding='utf-8', errors='replace')
                        matches[0].unlink(missing_ok=True)
                        cleaned = clean_vtt_text(raw_vtt)
                        if cleaned.strip():
                            return cleaned, source_type

        except Exception as e:
            logger.warning(f"yt-dlp subtitle attempt failed for {video_id}: {e}")

        return "", "none"

    async def get_transcript_report(self, url: str, lang: str, video_info: Dict[str, Any]) -> Tuple[Path, str]:
        """
        Extracts transcript, formats a full TXT report, saves it, and returns (file_path, preview_text).
        """
        video_id = video_info.get('video_id', 'video')
        text, source = await asyncio.to_thread(self._sync_fetch_transcript, url, lang, video_id)
        lang_title = "فارسی" if lang == "fa" else "English"

        if source == "none" or not text.strip():
            raise ValueError(
                f"متأسفانه برای این ویدیو هیچ متن یا گفتاری جهت استخراج پیدا نشد."
            )

        title = video_info.get('title', 'نامشخص')
        channel = video_info.get('channel', 'نامشخص')
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if source == "official":
            mode_desc = "زیرنویس رسمی ثبت‌شده توسط سازنده"
        elif source == "automatic":
            mode_desc = "تولید خودکار صدا به متن یوتیوب"
        else:
            mode_desc = "ترجمه هوشمند خودکار از متن اصلی ویدیو"

        report_lines = [
            "=" * 60,
            f"📄 متن و زیرنویس ویدیو: {title}",
            "=" * 60,
            f"👤 کانال / سازنده: {channel}",
            f"🌐 زبان متن: {lang_title} ({mode_desc})",
            f"📅 تاریخ استخراج: {now_str}",
            f"🔗 لینک ویدیو: https://youtu.be/{video_id}",
            "-" * 60,
            "",
            text,
            "",
            "=" * 60,
            "پایان متن استخراج شده",
            "=" * 60,
        ]
        full_report = "\n".join(report_lines)

        file_path = settings.transcript_dir / f"{video_id}_{lang}.txt"
        file_path.write_text(full_report, encoding='utf-8')

        preview = text[:300].replace('\n', ' ') + "..." if len(text) > 300 else text
        return file_path, preview
