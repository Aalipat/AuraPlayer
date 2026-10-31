from aiogram import Router
from src.bot.handlers.base import router as base_router
from src.bot.handlers.links import router as links_router
from src.bot.handlers.callbacks import router as callbacks_router

main_router = Router(name="main_router")
main_router.include_router(base_router)
main_router.include_router(links_router)
main_router.include_router(callbacks_router)

__all__ = ["main_router"]
