"""
Unit and integration tests for Phase 1 features:
- Native Opus tagging and Vorbis comments
- Opus embedded picture and ThumbnailService extraction
- Opus MIME type and Range streaming
- Download directory listing with Opus metadata
"""
import subprocess
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from mutagen.oggopus import OggOpus

from src.bot.config import settings
from src.services.media import MediaService, ThumbnailService
from src.panel.app import app, _sync_list_downloads, MEDIA_MIME_TYPES


@pytest.fixture
def temp_opus_file(tmp_path: Path) -> Path:
    """Creates a valid 1-second Opus file using bundled FFmpeg."""
    opus_path = tmp_path / "test_track.opus"
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=48000:cl=mono",
        "-t", "1",
        "-c:a", "libopus",
        str(opus_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return opus_path


@pytest.fixture
def sample_cover_image(tmp_path: Path) -> Path:
    """Creates a sample dummy JPEG image for cover art testing."""
    img_path = tmp_path / "cover.jpg"
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-f", "lavfi",
        "-i", "color=c=green:s=300x300",
        "-vframes", "1",
        str(img_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return img_path


@pytest.mark.asyncio
async def test_tag_opus_vorbis_comments_and_cover(temp_opus_file: Path, sample_cover_image: Path):
    """Verify that MediaService tags Opus files with Vorbis comments and embedded Picture."""
    media_service = MediaService()
    await media_service.tag_opus(
        opus_path=temp_opus_file,
        title="Modern Opus Track",
        artist="Test Artist",
        album="Phase 1 Album",
        year="2026",
        track_num=1,
        cover_image=sample_cover_image
    )

    audio = OggOpus(str(temp_opus_file))
    assert audio["title"] == ["Modern Opus Track"]
    assert audio["artist"] == ["Test Artist"]
    assert audio["album"] == ["Phase 1 Album"]
    assert audio["date"] == ["2026"]
    assert audio["tracknumber"] == ["1"]
    assert "metadata_block_picture" in audio
    assert len(audio["metadata_block_picture"]) > 0


@pytest.mark.asyncio
async def test_opus_thumbnail_extraction(temp_opus_file: Path, sample_cover_image: Path):
    """Verify that ThumbnailService extracts embedded cover art from an Opus file."""
    media_service = MediaService()
    await media_service.tag_opus(
        opus_path=temp_opus_file,
        title="Thumbnail Test",
        artist="Artist",
        cover_image=sample_cover_image
    )

    thumb = await ThumbnailService.get_thumbnail(temp_opus_file)
    assert thumb is not None
    assert thumb.exists()
    assert thumb.stat().st_size > 0


def test_opus_mime_type_registered():
    """Verify that .opus and .ogg have standard audio MIME types in app.py."""
    assert MEDIA_MIME_TYPES[".opus"] == "audio/opus"
    assert MEDIA_MIME_TYPES[".ogg"] == "audio/ogg"


@pytest.mark.asyncio
async def test_sync_list_downloads_with_opus(temp_opus_file: Path, sample_cover_image: Path):
    """Verify that _sync_list_downloads parses title and artist from tagged Opus files."""
    media_service = MediaService()
    await media_service.tag_opus(
        opus_path=temp_opus_file,
        title="Persian Traditional Opus",
        artist="Homayoun Shajarian",
        cover_image=sample_cover_image
    )

    file_list = _sync_list_downloads(temp_opus_file.parent)
    found = next((f for f in file_list if f["name"] == temp_opus_file.name), None)
    assert found is not None
    assert found["title"] == "Persian Traditional Opus"
    assert found["artist"] == "Homayoun Shajarian"
    assert found["ext"] == "opus"


@pytest.mark.asyncio
async def test_stream_opus_range_request(temp_opus_file: Path):
    """Verify that /api/media/stream supports partial range requests for Opus files."""
    # Place file in downloads folder to satisfy security check
    dest_file = settings.download_dir / temp_opus_file.name
    dest_file.write_bytes(temp_opus_file.read_bytes())

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/media/stream?path={dest_file}",
                headers={"Range": "bytes=0-100"}
            )
            assert resp.status_code == 206
            assert resp.headers["Content-Type"] == "audio/opus"
            assert "Content-Range" in resp.headers
            assert len(resp.content) == 101
    finally:
        if dest_file.exists():
            dest_file.unlink()
