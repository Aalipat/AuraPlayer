"""
Basic command handlers: /start, /help, and close callbacks.
"""
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

router = Router(name="base_router")


@router.message(CommandStart())
async def handle_start(message: Message):
    text = (
        "👋 **سلام! به ربات حرفه‌ای دانلود یوتیوب خوش آمدید.**\n\n"
        "🎬 **روش کار بسیار ساده است (بدون نیاز به تایپ):**\n"
        "فقط کافیست **لینک هر ویدیوی یوتیوب یا Shorts** را برای من بفرستید یا فوروارد کنید.\n\n"
        "✨ **امکانات:**\n"
        "• 🎥 دانلود ویدیو با کیفیت‌های ۳۶۰، ۴۸۰، ۷۲۰ و ۱۰۸۰\n"
        "• 🎵 استخراج و تبدیل به فایل صوتی MP3 همراه با کاور\n"
        "• 📝 استخراج متن و زیرنویس ویدیویی به فارسی و انگلیسی\n"
        "• ⚡ پخش آنلاین و مستقیم ویدیوها درون پلیر تلگرام\n\n"
        "👇 یک لینک یوتیوب بفرستید تا شروع کنیم!"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("help"))
async def handle_help(message: Message):
    text = (
        "📖 **راهنمای استفاده از ربات**\n\n"
        "۱. لینک ویدیوی مورد نظر در یوتیوب را کپی کنید.\n"
        "۲. لینک را در این چت بفرستید.\n"
        "۳. مشخصات ویدیو به همراه گزینه‌ها ظاهر می‌شود؛ کافیست روی کیفیت یا فایل دلخواه کلیک کنید.\n"
        "۴. بات به صورت خودکار فایل را آماده کرده و برای شما می‌فرستد."
    )
    await message.answer(text, parse_mode="Markdown")


@router.callback_query(F.data == "action:close")
async def handle_close_callback(callback: CallbackQuery):
    await callback.answer("بسته شد.")
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)
