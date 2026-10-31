"""
Interactive callback query handlers for download actions and panel navigation.
"""
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile
from aiogram.enums import ChatAction

from src.services.youtube import YouTubeService
from src.services.subtitles import SubtitleService
from src.services.media import MediaService
from src.bot.config import settings
from src.utils.formatting import format_bytes
from src.bot.keyboards import (
    build_main_selection_keyboard,
    build_video_quality_keyboard,
    build_subtitle_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="callbacks_router")


@router.callback_query(F.data.startswith("panel:"))
async def handle_panel_navigation(callback: CallbackQuery):
    parts = callback.data.split(":")
    menu_type = parts[1]
    video_id = parts[2]
    await callback.answer()

    if menu_type == "main":
        text = (
            "🎬 **لینک یوتیوب دریافت شد!**\n"
            f"🔗 `https://youtu.be/{video_id}`\n\n"
            "👇 لطفاً نوع محتوایی که می‌خواهید را انتخاب کنید:"
        )
        markup = build_main_selection_keyboard(video_id)
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)

    elif menu_type == "vid":
        text = (
            "🎥 **کیفیت ویدیو را انتخاب کنید:**\n\n"
            f"🔗 `https://youtu.be/{video_id}`\n\n"
            "👇 روی کیفیت مورد نظر کلیک کنید:"
        )
        markup = build_video_quality_keyboard(video_id)
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)

    elif menu_type == "sub":
        text = (
            "📝 **زبان متن و زیرنویس را انتخاب کنید:**\n\n"
            f"🔗 `https://youtu.be/{video_id}`\n\n"
            "👇 روی زبان مورد نظر کلیک کنید:"
        )
        markup = build_subtitle_keyboard(video_id)
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=markup)


@router.callback_query(F.data.startswith("dl:"))
async def handle_download_action(
    callback: CallbackQuery,
    youtube_service: YouTubeService,
    subtitle_service: SubtitleService,
    media_service: MediaService
):
    parts = callback.data.split(":")
    action_type = parts[1]
    canonical_url = ""
    temp_files = []

    try:
        if action_type == "vid":
            quality = parts[2]
            video_id = parts[3]
            canonical_url = f"https://www.youtube.com/watch?v={video_id}"

            await callback.answer(f"🎥 شروع دانلود {quality}p...", show_alert=False)
            status_text = f"⏳ **در حال دریافت و آماده‌سازی ویدیو ({quality}p)...**\nلطفاً صبور باشید."
            if callback.message.caption:
                await callback.message.edit_caption(caption=status_text, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=status_text, parse_mode="Markdown")

            # 1. Download Video
            video_path = await youtube_service.download_video(canonical_url, quality, video_id)
            temp_files.append(video_path)

            # 2. Check File Size & Compress if needed (> 48MB for Telegram bot limit)
            file_size_mb = video_path.stat().st_size / (1024 * 1024)
            if file_size_mb > settings.max_file_size_mb:
                compress_notice = (
                    f"⚙️ حجم ویدیو ({file_size_mb:.1f}MB) از سقف ۵۰ مگابایت تلگرام بیشتر است.\n"
                    "در حال فشرده‌سازی هوشمند بدون افت کیفیت..."
                )
                if callback.message.caption:
                    await callback.message.edit_caption(caption=compress_notice)
                else:
                    await callback.message.edit_text(text=compress_notice)

                compressed_path = await media_service.compress_video(video_path, settings.max_file_size_mb)
                if compressed_path != video_path:
                    temp_files.append(compressed_path)
                    video_path = compressed_path

            # 3. Extract dimensions & thumbnail
            width, height, duration = await media_service.get_video_metadata(video_path)
            thumb_path = settings.download_dir / f"thumb_{video_id}.jpg"
            extracted_thumb = await media_service.extract_thumbnail(video_path, thumb_path, at_second=3)
            if extracted_thumb:
                temp_files.append(extracted_thumb)

            # 4. Upload to Telegram with streaming flag
            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_VIDEO)
            tg_video = FSInputFile(str(video_path))
            tg_thumb = FSInputFile(str(extracted_thumb)) if extracted_thumb and extracted_thumb.exists() else None

            video_size_str = format_bytes(video_path.stat().st_size)
            await callback.message.reply_video(
                video=tg_video,
                caption=f"🎥 **کیفیت:** {quality}p | **حجم:** {video_size_str}\n🔗 [مشاهده در یوتیوب]({canonical_url})",
                parse_mode="Markdown",
                supports_streaming=True,
                duration=duration,
                width=width,
                height=height,
                thumbnail=tg_thumb
            )

            done_text = f"✅ **ارسال ویدیو ({quality}p) با موفقیت انجام شد.**"
            if callback.message.caption:
                await callback.message.edit_caption(caption=done_text, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=done_text, parse_mode="Markdown")

        elif action_type == "mp3":
            video_id = parts[2]
            canonical_url = f"https://www.youtube.com/watch?v={video_id}"

            await callback.answer("🎵 شروع استخراج صوت MP3...", show_alert=False)
            status_text = "⏳ **در حال استخراج و تبدیل به فایل صوتی MP3...**\nلطفاً صبور باشید."
            if callback.message.caption:
                await callback.message.edit_caption(caption=status_text, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=status_text, parse_mode="Markdown")

            info = {'title': f'Audio_{video_id}', 'channel': 'YouTube'}
            try:
                full_info = await youtube_service.get_info(canonical_url)
                info = full_info
            except Exception:
                pass

            mp3_path = await youtube_service.download_audio(canonical_url, video_id)
            temp_files.append(mp3_path)

            # Tag MP3
            await media_service.tag_mp3(mp3_path, title=info.get('title', 'Unknown'), artist=info.get('channel', 'YouTube'))

            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_VOICE)
            tg_audio = FSInputFile(str(mp3_path))
            audio_size_str = format_bytes(mp3_path.stat().st_size)

            await callback.message.reply_audio(
                audio=tg_audio,
                title=info.get('title', 'Unknown'),
                performer=info.get('channel', 'YouTube'),
                duration=info.get('duration', 0),
                caption=f"🎵 **{info.get('title', 'Unknown')}**\n👤 {info.get('channel', 'YouTube')} | 📦 {audio_size_str}"
            )

            done_text = "✅ **فایل صوتی MP3 با موفقیت ارسال شد.**"
            if callback.message.caption:
                await callback.message.edit_caption(caption=done_text, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=done_text, parse_mode="Markdown")

        elif action_type == "sub":
            lang = parts[2]
            video_id = parts[3]
            canonical_url = f"https://www.youtube.com/watch?v={video_id}"
            lang_label = "فارسی" if lang == "fa" else "English"

            await callback.answer(f"📝 استخراج زیرنویس {lang_label}...", show_alert=False)
            status_text = f"⏳ **در حال استخراج متن ویدیویی ({lang_label})...**\nلطفاً صبور باشید."
            if callback.message.caption:
                await callback.message.edit_caption(caption=status_text, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=status_text, parse_mode="Markdown")

            info = {'video_id': video_id, 'title': f'Video_{video_id}', 'channel': 'YouTube'}
            try:
                full_info = await youtube_service.get_info(canonical_url)
                info = full_info
            except Exception:
                pass

            report_file, preview = await subtitle_service.get_transcript_report(canonical_url, lang, info)
            temp_files.append(report_file)

            await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
            tg_doc = FSInputFile(str(report_file))

            caption = (
                f"📄 **متن و زیرنویس استخراج‌شده ({lang_label})**\n\n"
                f"📝 **بخشی از متن:**\n_{preview}_\n\n"
                f"📎 فایل متنی کامل ضمیمه شد."
            )
            await callback.message.reply_document(
                document=tg_doc,
                caption=caption,
                parse_mode="Markdown"
            )

            done_text = f"✅ **متن زیرنویس ({lang_label}) با موفقیت ارسال شد.**"
            if callback.message.caption:
                await callback.message.edit_caption(caption=done_text, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=done_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Download/process error: {e}", exc_info=True)
        error_msg = f"❌ **خطا در پردازش درخواست:**\n`{str(e)[:250]}`"
        try:
            if callback.message.caption:
                await callback.message.edit_caption(caption=error_msg, parse_mode="Markdown")
            else:
                await callback.message.edit_text(text=error_msg, parse_mode="Markdown")
        except Exception:
            await callback.message.reply(error_msg, parse_mode="Markdown")

    finally:
        # Strict cleanup of temporary files
        for p in temp_files:
            try:
                if isinstance(p, Path) and p.exists():
                    p.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"Failed to delete temp file {p}: {e}")
