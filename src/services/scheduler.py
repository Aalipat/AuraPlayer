"""
Night Scheduler & Bandwidth Limiter Service.
Manages a persistent download queue (data/queue.json) and an asynchronous
scheduler loop for timed downloads with rate limiting.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.bot.config import settings

logger = logging.getLogger(__name__)

QUEUE_FILE = settings.data_dir / "queue.json"


class SchedulerService:
    def __init__(self, queue_file: Path = QUEUE_FILE):
        self.queue_file = queue_file
        self._lock = asyncio.Lock()
        self._loop_task: Optional[asyncio.Task] = None
        self._is_running = False

    def load_config(self) -> Dict[str, Any]:
        """Loads scheduler configuration and queued items from disk."""
        if self.queue_file.exists():
            try:
                data = json.loads(self.queue_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("schedule_enabled", False)
                    data.setdefault("schedule_start", "02:00")
                    data.setdefault("schedule_end", "07:00")
                    data.setdefault("bandwidth_limit_kbps", 0)
                    data.setdefault("items", [])
                    return data
            except Exception as e:
                logger.warning(f"Failed to read queue.json: {e}")

        return {
            "schedule_enabled": False,
            "schedule_start": "02:00",
            "schedule_end": "07:00",
            "bandwidth_limit_kbps": 0,
            "items": [],
        }

    def save_config(self, cfg: Dict[str, Any]) -> None:
        """Saves scheduler configuration to disk atomically."""
        try:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.queue_file.with_suffix(".tmp")
            temp_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_file.replace(self.queue_file)
        except Exception as e:
            logger.error(f"Failed to save queue.json: {e}")

    @staticmethod
    def is_in_window(start_str: str, end_str: str, now_dt: Optional[datetime] = None) -> bool:
        """
        Determines whether the current (or given) time falls within the window.
        Correctly handles cross-midnight intervals (e.g. 23:00 to 06:00).
        """
        try:
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            start_t = time(sh, sm)
            end_t = time(eh, em)
        except Exception:
            return False

        current_t = (now_dt or datetime.now()).time()

        if start_t <= end_t:
            return start_t <= current_t <= end_t
        else:
            # Window spans past midnight
            return current_t >= start_t or current_t <= end_t

    def add_item(
        self,
        url: str,
        type_: str,
        quality: Optional[str] = "720",
        lang: Optional[str] = "fa",
        title: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Adds a download task item to the persistent queue with smart type detection."""
        cfg = self.load_config()
        
        # Smart action_type inference
        clean_url = url.strip()
        detected_type = type_
        if "open.spotify.com" in clean_url:
            if "/playlist/" in clean_url or "/album/" in clean_url:
                detected_type = "spotify_batch"
            else:
                detected_type = "spotify_audio"
        elif "youtube.com" in clean_url or "youtu.be" in clean_url:
            if "list=" in clean_url and ("playlist" in clean_url or "watch" in clean_url):
                detected_type = "youtube_batch"
            elif type_ not in ("audio", "video", "subtitle"):
                detected_type = "video"

        item = {
            "id": f"q_{uuid.uuid4().hex[:8]}",
            "url": clean_url,
            "type": detected_type,
            "quality": quality or "720",
            "lang": lang or "fa",
            "title": title or clean_url,
            "extra": extra or {},
            "status": "pending",  # pending, running, completed, error, cancelled
            "added_at": datetime.now().isoformat(),
            "completed_at": None,
            "error": None,
            "task_id": None,
        }
        cfg["items"].append(item)
        self.save_config(cfg)
        return item

    def remove_item(self, item_id: str) -> bool:
        """Removes an item from the queue by ID."""
        cfg = self.load_config()
        initial_len = len(cfg["items"])
        cfg["items"] = [it for it in cfg["items"] if it["id"] != item_id]
        if len(cfg["items"]) < initial_len:
            self.save_config(cfg)
            return True
        return False

    def clear_completed(self) -> int:
        """Removes completed and errored items from the queue."""
        cfg = self.load_config()
        initial_len = len(cfg["items"])
        cfg["items"] = [it for it in cfg["items"] if it.get("status") in ("pending", "running")]
        removed = initial_len - len(cfg["items"])
        if removed > 0:
            self.save_config(cfg)
        return removed

    def update_config(
        self,
        enabled: Optional[bool] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        bandwidth_limit_kbps: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Updates scheduler settings."""
        cfg = self.load_config()
        if enabled is not None:
            cfg["schedule_enabled"] = bool(enabled)
        if start is not None:
            cfg["schedule_start"] = str(start)
        if end is not None:
            cfg["schedule_end"] = str(end)
        if bandwidth_limit_kbps is not None:
            cfg["bandwidth_limit_kbps"] = max(0, int(bandwidth_limit_kbps))
        self.save_config(cfg)
        return cfg

    async def execute_item(self, item: Dict[str, Any], task_manager) -> None:
        """Executes a single queue item via task_manager."""
        cfg = self.load_config()
        # Find item in fresh config
        found = next((it for it in cfg.get("items", []) if it["id"] == item["id"]), None)
        if not found or found.get("status") == "running":
            return

        found["status"] = "running"
        self.save_config(cfg)

        extra = dict(found.get("extra") or {})
        bw_limit = cfg.get("bandwidth_limit_kbps", 0)
        if bw_limit > 0:
            extra["ratelimit_kbps"] = bw_limit

        try:
            tid = task_manager.create_task(
                url=found["url"],
                action_type=found["type"],
                quality=found.get("quality", "720"),
                lang=found.get("lang", "fa"),
                extra=extra,
            )
            found["task_id"] = tid
            self.save_config(cfg)

            while True:
                await asyncio.sleep(2)
                t = task_manager.get_task(tid)
                if not t or t.get("is_done"):
                    cfg = self.load_config()
                    target = next((it for it in cfg.get("items", []) if it["id"] == item["id"]), found)
                    if t and t.get("error"):
                        target["status"] = "error"
                        target["error"] = t.get("error")
                    else:
                        target["status"] = "completed"
                    target["completed_at"] = datetime.now().isoformat()
                    self.save_config(cfg)
                    break
        except Exception as ex:
            cfg = self.load_config()
            target = next((it for it in cfg.get("items", []) if it["id"] == item["id"]), found)
            target["status"] = "error"
            target["error"] = str(ex)
            self.save_config(cfg)

    async def force_start_item(self, item_id: str, task_manager) -> bool:
        """Starts downloading a specific queued item immediately."""
        cfg = self.load_config()
        item = next((it for it in cfg.get("items", []) if it["id"] == item_id), None)
        if not item or item.get("status") == "running":
            return False

        asyncio.create_task(self.execute_item(item, task_manager))
        return True

    async def force_start_all(self, task_manager) -> int:
        """Starts downloading all pending items immediately."""
        cfg = self.load_config()
        pending = [it for it in cfg.get("items", []) if it.get("status") == "pending"]
        for it in pending:
            asyncio.create_task(self.execute_item(it, task_manager))
        return len(pending)

    async def start_background_loop(self, task_manager) -> None:
        """
        Background asyncio daemon that periodically checks the queue and executes
        pending tasks whenever the current time falls inside the scheduled window.
        """
        self._is_running = True
        logger.info("SchedulerService background loop started.")

        while self._is_running:
            try:
                cfg = self.load_config()
                is_enabled = cfg.get("schedule_enabled", False)
                start_str = cfg.get("schedule_start", "02:00")
                end_str = cfg.get("schedule_end", "07:00")

                if is_enabled and self.is_in_window(start_str, end_str):
                    pending = [it for it in cfg.get("items", []) if it.get("status") == "pending"]
                    if pending:
                        item = pending[0]
                        logger.info(f"Scheduler triggered night download for {item['id']}: {item['url']}")
                        await self.execute_item(item, task_manager)

            except Exception as e:
                logger.error(f"Error in scheduler background loop: {e}", exc_info=True)

            await asyncio.sleep(20)

    def stop(self) -> None:
        self._is_running = False


scheduler_service = SchedulerService()
