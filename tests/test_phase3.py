import json
import pytest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.panel.app import app
from src.bot.config import settings
from src.services.media import WaveformService
from src.services.lyrics import LyricsService, parse_lrc, normalize_track_metadata
from src.services.scheduler import SchedulerService
from src.panel.tasks import _VIDEO_ID_PATTERN, _get_existing_video_ids, task_manager

client = TestClient(app)

# -------------------------------------------------------------
# 1. Waveform Tests
# -------------------------------------------------------------
def test_waveform_astats_mock(tmp_path):
    media_file = tmp_path / "test_track.mp3"
    media_file.write_bytes(b"dummy audio data")

    # Mock ffmpeg astats output with RMS levels
    astats_stderr = """
[Parsed_astats_0 @ 000001] lavfi.astats.Overall.RMS_level=-24.50
[Parsed_astats_0 @ 000001] lavfi.astats.Overall.RMS_level=-12.20
[Parsed_astats_0 @ 000001] lavfi.astats.Overall.RMS_level=-18.40
[Parsed_astats_0 @ 000001] lavfi.astats.Overall.RMS_level=-6.10
"""
    with patch("subprocess.run") as mock_sub, patch("subprocess.check_output") as mock_probe:
        mock_probe.return_value = b'{"format": {"duration": "120.0"}}'
        mock_sub.return_value = MagicMock(returncode=0, stderr=astats_stderr, stdout="")
        
        peaks = WaveformService._sync_extract_peaks(media_file, samples=30)
        assert len(peaks) == 30
        assert all(0.08 <= x <= 1.0 for x in peaks)

def test_waveform_api_endpoint(tmp_path):
    test_file = settings.download_dir / "temp_audio_test.mp3"
    test_file.write_bytes(b"sample audio")
    
    try:
        with patch.object(WaveformService, "get_or_create_waveform", return_value=[0.35] * 120):
            res = client.get(f"/api/media/waveform?path={test_file.resolve()}&samples=120")
            assert res.status_code == 200
            data = res.json()
            assert len(data["peaks"]) == 120
            assert data["samples"] == 120

            # Path traversal forbidden check
            outside_file = tmp_path / "secret.mp3"
            outside_file.write_bytes(b"forbidden")
            bad_res = client.get(f"/api/media/waveform?path={outside_file.resolve()}")
            assert bad_res.status_code == 403
    finally:
        if test_file.exists():
            test_file.unlink()

# -------------------------------------------------------------
# 2. Lyrics Tests (LRC Parsing & Caching)
# -------------------------------------------------------------
def test_lyrics_lrc_parser():
    lrc_content = """[ti:Never Gonna Give You Up]
[ar:Rick Astley]
[00:18.50]We're no strangers to love
[00:22.80]You know the rules and so do I
[00:27.10]A full commitment's what I'm thinking of
"""
    parsed = parse_lrc(lrc_content)
    assert len(parsed) == 3
    assert parsed[0]["time"] == 18.5
    assert parsed[0]["text"] == "We're no strangers to love"
    assert parsed[1]["time"] == 22.8
    assert parsed[2]["time"] == 27.1

def test_lyrics_lrc_sorting_and_empty_lines():
    lrc_content = """
[00:30.00]Third line
[00:10.00]First line
[00:20.00]Second line
[invalid line without tags]
"""
    parsed = parse_lrc(lrc_content)
    assert len(parsed) == 3
    assert [p["text"] for p in parsed] == ["First line", "Second line", "Third line"]

@pytest.mark.asyncio
async def test_lyrics_local_file_detection(tmp_path):
    media_file = tmp_path / "my_song.mp3"
    media_file.write_bytes(b"audio")
    lrc_file = tmp_path / "my_song.lrc"
    lrc_file.write_text("[00:05.00]Intro line\n[00:12.00]Verse line", encoding="utf-8")

    lyrics = await LyricsService.get_lyrics(media_path=media_file)
    assert lyrics is not None
    assert lyrics["is_synced"] is True
    assert len(lyrics["lines"]) == 2
    assert lyrics["lines"][0]["text"] == "Intro line"

def test_lyrics_api_endpoint(tmp_path):
    test_media = settings.download_dir / "track_lyrics_test.mp3"
    test_media.write_bytes(b"dummy")
    test_lrc = settings.download_dir / "track_lyrics_test.lrc"
    test_lrc.write_text("[00:01.00]Hello world", encoding="utf-8")

    try:
        res = client.get(f"/api/media/lyrics?path={test_media.resolve()}")
        assert res.status_code == 200
        data = res.json()
        assert data["is_synced"] is True
        assert len(data["lines"]) == 1
        assert data["lines"][0]["text"] == "Hello world"

        # Traversal test
        outside_file = tmp_path / "outside.mp3"
        outside_file.write_bytes(b"outside")
        res_traversal = client.get(f"/api/media/lyrics?path={outside_file.resolve()}")
        assert res_traversal.status_code == 200  # Fallback to metadata search when target is outside
    finally:
        if test_media.exists():
            test_media.unlink()
        if test_lrc.exists():
            test_lrc.unlink()

# -------------------------------------------------------------
# 3. Night Scheduler & Bandwidth Window Logic
# -------------------------------------------------------------
def test_scheduler_window_logic():
    # Daytime window: 10:00 to 14:00
    dt_12 = datetime(2026, 10, 31, 12, 0)
    dt_09 = datetime(2026, 10, 31, 9, 59)
    dt_15 = datetime(2026, 10, 31, 15, 1)

    assert SchedulerService.is_in_window("10:00", "14:00", dt_12) is True
    assert SchedulerService.is_in_window("10:00", "14:00", dt_09) is False
    assert SchedulerService.is_in_window("10:00", "14:00", dt_15) is False

    # Cross-midnight window: 23:00 to 04:00
    dt_2330 = datetime(2026, 10, 31, 23, 30)
    dt_0115 = datetime(2026, 10, 31, 1, 15)
    dt_0401 = datetime(2026, 10, 31, 4, 1)
    dt_1200 = datetime(2026, 10, 31, 12, 0)

    assert SchedulerService.is_in_window("23:00", "04:00", dt_2330) is True
    assert SchedulerService.is_in_window("23:00", "04:00", dt_0115) is True
    assert SchedulerService.is_in_window("23:00", "04:00", dt_0401) is False
    assert SchedulerService.is_in_window("23:00", "04:00", dt_1200) is False

    # Early morning window: 02:00 to 07:00
    dt_0330 = datetime(2026, 10, 31, 3, 30)
    dt_0159 = datetime(2026, 10, 31, 1, 59)
    assert SchedulerService.is_in_window("02:00", "07:00", dt_0330) is True
    assert SchedulerService.is_in_window("02:00", "07:00", dt_0159) is False

def test_scheduler_queue_management(tmp_path):
    queue_file = tmp_path / "test_queue.json"
    service = SchedulerService(queue_file=queue_file)
    
    # Add item
    item = service.add_item("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "audio", "high", title="Never Gonna Give You Up")
    assert item["status"] == "pending"
    assert item["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    
    cfg = service.load_config()
    assert len(cfg["items"]) == 1
    assert cfg["items"][0]["id"] == item["id"]

    # Update config
    service.update_config(enabled=True, start="02:30", end="07:30", bandwidth_limit_kbps=300)
    cfg2 = service.load_config()
    assert cfg2["schedule_enabled"] is True
    assert cfg2["schedule_start"] == "02:30"
    assert cfg2["bandwidth_limit_kbps"] == 300

    # Remove item
    removed = service.remove_item(item["id"])
    assert removed is True
    assert len(service.load_config()["items"]) == 0

def test_queue_endpoints():
    res = client.get("/api/queue")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "schedule_enabled" in data

    # Add item
    add_res = client.post("/api/queue/add", json={
        "url": "https://www.youtube.com/watch?v=oHg5SJYRHA0",
        "type": "video",
        "quality": "1080p",
        "title": "RickRoll Test"
    })
    assert add_res.status_code == 200
    item = add_res.json()["item"]
    item_id = item["id"]

    # Update config
    cfg_res = client.post("/api/queue/config", json={
        "schedule_enabled": False,
        "schedule_start": "03:00",
        "schedule_end": "06:00",
        "bandwidth_limit_kbps": 250
    })
    assert cfg_res.status_code == 200
    assert cfg_res.json()["config"]["bandwidth_limit_kbps"] == 250

    # Remove item
    del_res = client.post("/api/queue/remove", json={"id": item_id})
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

# -------------------------------------------------------------
# 4. Playlist Delta Sync & Rate Limiting Options
# -------------------------------------------------------------
def test_delta_sync_video_id_regex():
    sample1 = "Rick Astley - Never Gonna Give You Up [dQw4w9WgXcQ].mp3"
    match1 = _VIDEO_ID_PATTERN.search(sample1)
    assert match1 is not None
    assert match1.group(1) == "dQw4w9WgXcQ"

    sample2 = "Tutorial Part 1 [_aB12-XyZ90].mp4"
    match2 = _VIDEO_ID_PATTERN.search(sample2)
    assert match2 is not None
    assert match2.group(1) == "_aB12-XyZ90"

    sample3 = "Track Without Id.mp3"
    assert _VIDEO_ID_PATTERN.search(sample3) is None

def test_delta_sync_existing_scanner(tmp_path):
    (tmp_path / "Song 1 [vid11111111].mp3").write_bytes(b"x")
    (tmp_path / "Song 2 [vid22222222].mp4").write_bytes(b"y")
    (tmp_path / "Regular Song.mp3").write_bytes(b"z")

    found_ids = _get_existing_video_ids(tmp_path)
    assert "vid11111111" in found_ids
    assert "vid22222222" in found_ids
    assert len(found_ids) == 2

def test_rate_limit_ydl_opts():
    opts_no_limit = task_manager._get_common_ydl_opts(out_tmpl="%(title)s.%(ext)s")
    assert "ratelimit" not in opts_no_limit

    opts_with_limit = task_manager._get_common_ydl_opts(out_tmpl="%(title)s.%(ext)s", ratelimit_kbps=500)
    assert opts_with_limit.get("ratelimit") == 500 * 1024

# -------------------------------------------------------------
# 5. Phase 3 Refinement Tests: Normalizer & Smart Queue Execution
# -------------------------------------------------------------
def test_normalize_track_metadata():
    # Video ID removal and hyphen artist/title separation
    t1, a1 = normalize_track_metadata("OneRepublic - Counting Stars [dQw4w9WgXcQ].mp3")
    assert t1 == "Counting Stars"
    assert a1 == "OneRepublic"

    # Underscores and Official Video tags
    t2, a2 = normalize_track_metadata("Coldplay_-_Yellow_(Official_Video)")
    assert t2 == "Yellow"
    assert a2 == "Coldplay"

    # Title without artist separator
    t3, a3 = normalize_track_metadata("Shape of You [Music Video]")
    assert t3 == "Shape of You"
    assert a3 == ""

    # Explicit artist preserved if provided
    t4, a4 = normalize_track_metadata("Bohemian Rhapsody [id123456789]", artist="Queen")
    assert t4 == "Bohemian Rhapsody"
    assert a4 == "Queen"

def test_scheduler_smart_type_detection(tmp_path):
    queue_file = tmp_path / "smart_queue.json"
    service = SchedulerService(queue_file=queue_file)

    # Spotify track -> spotify_audio
    it_sp_track = service.add_item("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", "audio")
    assert it_sp_track["type"] == "spotify_audio"

    # Spotify playlist -> spotify_batch
    it_sp_plist = service.add_item("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "audio")
    assert it_sp_plist["type"] == "spotify_batch"

    # YouTube playlist -> youtube_batch
    it_yt_plist = service.add_item("https://www.youtube.com/playlist?list=PLrAl5G_bB9c51234", "video")
    assert it_yt_plist["type"] == "youtube_batch"

    # Direct YouTube video -> preserves or defaults
    it_yt_vid = service.add_item("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video")
    assert it_yt_vid["type"] == "video"

def test_scheduler_clear_completed(tmp_path):
    queue_file = tmp_path / "clear_queue.json"
    service = SchedulerService(queue_file=queue_file)

    it1 = service.add_item("https://example.com/1", "audio")
    it2 = service.add_item("https://example.com/2", "audio")
    it3 = service.add_item("https://example.com/3", "audio")

    cfg = service.load_config()
    cfg["items"][0]["status"] = "completed"
    cfg["items"][1]["status"] = "error"
    cfg["items"][2]["status"] = "pending"
    service.save_config(cfg)

    removed = service.clear_completed()
    assert removed == 2

    remaining = service.load_config()["items"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == it3["id"]
    assert remaining[0]["status"] == "pending"

def test_queue_start_now_and_clear_endpoints():
    # Add pending item to queue
    add_res = client.post("/api/queue/add", json={
        "url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
        "type": "audio",
        "quality": "320k"
    })
    assert add_res.status_code == 200
    item = add_res.json()["item"]
    assert item["type"] == "spotify_audio"
    item_id = item["id"]

    # Test start-now endpoint
    with patch.object(SchedulerService, "force_start_item", return_value=True):
        sn_res = client.post("/api/queue/start-now", json={"id": item_id})
        assert sn_res.status_code == 200
        assert sn_res.json()["success"] is True

    # Test start-all endpoint
    with patch.object(SchedulerService, "force_start_all", return_value=3):
        sa_res = client.post("/api/queue/start-all")
        assert sa_res.status_code == 200
        assert sa_res.json()["started_count"] == 3

    # Test clear-completed endpoint
    with patch.object(SchedulerService, "clear_completed", return_value=2):
        cc_res = client.post("/api/queue/clear-completed")
        assert cc_res.status_code == 200
        assert cc_res.json()["cleared"] == 2

    # Cleanup item
    client.post("/api/queue/remove", json={"id": item_id})


def test_lyrics_persian_companion_pairing():
    media = settings.download_dir / "track_persian_test.mp3"
    media.write_bytes(b"dummy")
    lrc = settings.download_dir / "track_persian_test.lrc"
    lrc.write_text("[00:05.00]Hello\n[00:10.00]World", encoding="utf-8")
    fa_lrc = settings.download_dir / "track_persian_test_persian.lrc"
    fa_lrc.write_text("[00:05.00]سلام\n[00:10.00]دنیا", encoding="utf-8")

    try:
        res = client.get(f"/api/media/lyrics?path={media.resolve()}")
        assert res.status_code == 200
        data = res.json()
        assert data["is_synced"] is True
        assert len(data["lines"]) == 2
        assert data["lines"][0]["text"] == "Hello"
        assert data["lines"][0]["fa_text"] == "سلام"
        assert data["lines"][1]["text"] == "World"
        assert data["lines"][1]["fa_text"] == "دنیا"
        assert len(data["persian_lines"]) == 2
    finally:
        for f in (media, lrc, fa_lrc):
            if f.exists():
                f.unlink()


def test_unified_spotify_lyrics_endpoint():
    dummy_lyrics = {
        "success": True,
        "has_lyrics": True,
        "is_synced": True,
        "clean_title": "Counting Stars",
        "clean_artist": "OneRepublic",
        "lines": [{"time": 5.0, "text": "Lately I been losing sleep", "fa_text": "اخیرا خواب از سرم پریده"}],
        "plain_lyrics": "Lately I been losing sleep",
        "synced_lyrics": "[00:05.00]Lately I been losing sleep",
        "persian_lines": [{"time": 5.0, "text": "اخیرا خواب از سرم پریده"}],
        "persian_plain": "اخیرا خواب از سرم پریده",
        "persian_synced": "[00:05.00]اخیرا خواب از سرم پریده",
        "bilingual": "[00:05.00]Lately I been losing sleep\n(اخیرا خواب از سرم پریده)"
    }
    with patch("src.services.lyrics.LyricsService.get_lyrics", return_value=dummy_lyrics):
        res = client.post("/api/spotify/lyrics", json={
            "url": "",
            "title": "OneRepublic_-_Counting_Stars [Official Video]",
            "artist": "OneRepublic",
            "duration": 257
        })
        assert res.status_code == 200
        data = res.json()
        assert data["has_lyrics"] is True
        assert data["clean_title"] == "Counting Stars"
        assert data["lines"][0]["fa_text"] == "اخیرا خواب از سرم پریده"
        assert data["persian_plain"] == "اخیرا خواب از سرم پریده"

