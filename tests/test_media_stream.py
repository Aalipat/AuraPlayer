"""
Automated unit & integration tests for Media Streaming (HTTP 206 Range Requests)
and Thumbnail Extraction Engine.
"""
import pytest
from pathlib import Path
from httpx import ASGITransport, AsyncClient
from src.panel.app import app
from src.services.media import ThumbnailService
from src.bot.config import settings


@pytest.mark.asyncio
async def test_thumbnail_companion_extraction(tmp_path: Path):
    # Setup test audio file and companion image
    test_media = tmp_path / "test_track.mp3"
    test_media.write_bytes(b"dummy mp3 payload 12345")
    companion_img = tmp_path / "test_track.jpg"
    companion_img.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)  # Dummy JPEG magic bytes

    cached = await ThumbnailService.get_thumbnail(test_media)
    assert cached is not None
    assert cached.exists()
    assert cached.stat().st_size > 0
    # Clean up cached thumb
    if cached.exists():
        cached.unlink()


@pytest.mark.asyncio
async def test_media_thumbnail_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Non-existent file inside download_dir -> 404
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        nonexistent = settings.download_dir / "nonexistent_fake_track_123.mp3"
        resp = await client.get(f"/api/media/thumbnail?path={str(nonexistent)}")
        assert resp.status_code == 404

        # 2. Path traversal attack or outside download_dir -> 403 Forbidden
        resp_attack = await client.get("/api/media/thumbnail?path=..%2F..%2Fwindows%2Fwin.ini")
        assert resp_attack.status_code == 403


@pytest.mark.asyncio
async def test_media_stream_http_range_requests(tmp_path: Path):
    # Create test media file inside download_dir
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    test_file = settings.download_dir / "range_test_file.mp3"
    content = b"ABCDEFGHIJ" * 100  # 1000 bytes
    test_file.write_bytes(content)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        safe_path = str(test_file)

        # 1. Full content request (200 OK)
        resp_full = await client.get(f"/api/media/stream?path={safe_path}")
        assert resp_full.status_code == 200
        assert resp_full.headers.get("accept-ranges") == "bytes"
        assert len(resp_full.content) == 1000

        # 2. HEAD request
        resp_head = await client.head(f"/api/media/stream?path={safe_path}")
        assert resp_head.status_code == 200
        assert resp_head.headers.get("accept-ranges") == "bytes"

        # 3. Partial Range Request (bytes 0-199) -> 206 Partial Content
        resp_part1 = await client.get(
            f"/api/media/stream?path={safe_path}",
            headers={"Range": "bytes=0-199"}
        )
        assert resp_part1.status_code == 206
        assert resp_part1.headers.get("content-range") == "bytes 0-199/1000"
        assert len(resp_part1.content) == 200
        assert resp_part1.content == content[:200]

        # 4. Partial Range Request (bytes 500-) -> 206 Partial Content
        resp_part2 = await client.get(
            f"/api/media/stream?path={safe_path}",
            headers={"Range": "bytes=500-"}
        )
        assert resp_part2.status_code == 206
        assert resp_part2.headers.get("content-range") == "bytes 500-999/1000"
        assert len(resp_part2.content) == 500
        assert resp_part2.content == content[500:]

        # 5. Invalid range -> 416 Range Not Satisfiable
        resp_invalid = await client.get(
            f"/api/media/stream?path={safe_path}",
            headers={"Range": "bytes=5000-6000"}
        )
        assert resp_invalid.status_code == 416

    # Clean up test file
    if test_file.exists():
        test_file.unlink()


@pytest.mark.asyncio
async def test_downloads_endpoint_metadata():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/downloads")
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert isinstance(data["files"], list)
        for f in data["files"]:
            assert "name" in f
            assert "path" in f
            assert "size" in f
            assert "ext" in f
            assert "cover_url" in f
