"""
YouTube link detection and instant panel dispatcher.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message

from src.services.youtube import is_valid_youtube_url, extract_video_id
from src.bot.keyboards import build_main_selection_keyboard

logger = logging.getLogger(__name__)
router = Router(name="links_router")


@router.message(F.text)
async def handle_potential_link(message: Message):
    text = message.text.strip()
    if not is_valid_youtube_url(text):
        return  # Ignore non-youtube text

    video_id = extract_video_id(text)
    if not video_id:
        return

    text_msg = (
        "🎬 **لینک یوتیوب دریافت شد!**\n"
        f"🔗 `https://youtu.be/{video_id}`\n\n"
        "👇 لطفاً نوع محتوایی که می‌خواهید را انتخاب کنید:"
    )
    markup = build_main_selection_keyboard(video_id)
    await message.reply(text_msg, parse_mode="Markdown", reply_markup=markup)
