"""
Throttling middleware to prevent concurrent request spam.
"""
import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit_seconds: float = 3.0):
        self.rate_limit_seconds = rate_limit_seconds
        self.last_actions: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith("dl:"):
            user_id = event.from_user.id
            now = time.time()
            last_time = self.last_actions.get(user_id, 0)
            if now - last_time < self.rate_limit_seconds:
                await event.answer("⏳ لطفاً کمی صبر کنید! درخواست قبلی در حال پردازش است.", show_alert=True)
                return None
            self.last_actions[user_id] = now

        return await handler(event, data)
