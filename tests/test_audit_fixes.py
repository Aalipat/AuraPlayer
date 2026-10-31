import asyncio
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from src.panel.app import app
from src.panel.tasks import task_manager
from src.bot.config import settings
import yt_dlp

client = TestClient(app)

def test_logger_defined_in_app():
    import src.panel.app as app_module
    assert hasattr(app_module, "logger")
    assert app_module.logger is not None

def test_path_traversal_protection_stream(tmp_path):
    # Try accessing a file outside settings.download_dir
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("classified")
    
    res = client.get(f"/api/media/stream?path={outside_file.resolve()}")
    assert res.status_code == 403

def test_path_traversal_protection_delete(tmp_path):
    outside_file = tmp_path / "do_not_delete.txt"
    outside_file.write_text("data")
    
    res = client.post("/api/downloads/delete", json={"path": str(outside_file.resolve())})
    assert res.status_code == 403
    assert outside_file.exists()

def test_task_cancellation_sets_flag():
    task_id = task_manager.create_task("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video")
    assert task_id in task_manager.tasks
    
    cancelled = task_manager.cancel_task(task_id)
    assert cancelled is True
    assert task_manager.tasks[task_id].get("is_cancelled") is True
    
    # Check progress hook throws DownloadCancelled
    hook = task_manager._make_progress_hook(task_id)
    with pytest.raises(yt_dlp.utils.DownloadCancelled):
        hook({"status": "downloading", "total_bytes": 1000, "downloaded_bytes": 100})

@pytest.mark.asyncio
async def test_task_dismiss_cleans_async_tasks():
    task_id = task_manager.create_task("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "audio")
    dummy_task = asyncio.create_task(asyncio.sleep(0.01))
    task_manager.register_async_task(task_id, dummy_task)
    assert task_id in task_manager._async_tasks
    
    task_manager.dismiss_task(task_id)
    assert task_id not in task_manager.tasks
    assert task_id not in task_manager._async_tasks
    await dummy_task
