"""
Interactive inline keyboard builder for YouTube downloads.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_main_selection_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """
    Instant selection panel shown immediately upon receiving a link:
    - 🎥 دانلود ویدیو
    - 🎵 دانلود فایل صوتی (MP3)
    - 📝 استخراج متن و زیرنویس
    - ❌ بستن
    """
    keyboard = [
        [
            InlineKeyboardButton(text="🎥 دانلود ویدیو", callback_data=f"panel:vid:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="🎵 دانلود فایل صوتی (MP3)", callback_data=f"dl:mp3:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="📝 استخراج متن و زیرنویس", callback_data=f"panel:sub:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ بستن", callback_data="action:close"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_video_quality_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """
    Quality selection panel for video downloads:
    - 360p | 480p
    - 720p | 1080p
    - 🔙 بازگشت
    """
    keyboard = [
        [
            InlineKeyboardButton(text="🎥 360p", callback_data=f"dl:vid:360:{video_id}"),
            InlineKeyboardButton(text="🎥 480p", callback_data=f"dl:vid:480:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="🎥 720p (HD)", callback_data=f"dl:vid:720:{video_id}"),
            InlineKeyboardButton(text="🎥 1080p (FHD)", callback_data=f"dl:vid:1080:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data=f"panel:main:{video_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_subtitle_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """
    Language selection panel for subtitles/transcripts:
    - 🇮🇷 متن فارسی
    - 🇬🇧 English Sub
    - 🔙 بازگشت
    """
    keyboard = [
        [
            InlineKeyboardButton(text="🇮🇷 متن فارسی", callback_data=f"dl:sub:fa:{video_id}"),
            InlineKeyboardButton(text="🇬🇧 English Sub", callback_data=f"dl:sub:en:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 بازگشت به منوی اصلی", callback_data=f"panel:main:{video_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ انصراف", callback_data="action:close")]]
    )
