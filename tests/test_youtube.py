"""
Automated unit & integration tests for YouTube Playlist & Video functionality.
"""
import pytest
from src.services.youtube import (
    YouTubeService,
    extract_video_id,
    extract_playlist_id,
    is_valid_youtube_url,
    is_valid_youtube_playlist_url,
)
from src.panel.app import app
from httpx import ASGITransport, AsyncClient


def test_youtube_url_parsing():
    # 1. Single video URLs
    watch_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert extract_video_id(watch_url) == "dQw4w9WgXcQ"
    assert is_valid_youtube_url(watch_url) is True
    assert is_valid_youtube_playlist_url(watch_url) is False

    short_url = "https://youtu.be/dQw4w9WgXcQ"
    assert extract_video_id(short_url) == "dQw4w9WgXcQ"
    assert is_valid_youtube_url(short_url) is True

    shorts_url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
    assert extract_video_id(shorts_url) == "dQw4w9WgXcQ"
    assert is_valid_youtube_url(shorts_url) is True

    # 2. Pure Playlist URLs
    pl_url1 = "https://www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert extract_playlist_id(pl_url1) == "PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert is_valid_youtube_playlist_url(pl_url1) is True
    assert is_valid_youtube_url(pl_url1) is True

    pl_url2 = "https://music.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj&si=test"
    assert extract_playlist_id(pl_url2) == "PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert is_valid_youtube_playlist_url(pl_url2) is True

    pl_url3 = "www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert extract_playlist_id(pl_url3) == "PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert is_valid_youtube_playlist_url(pl_url3) is True

    # 3. Video URL with attached Playlist param
    combo_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj&index=2"
    assert extract_video_id(combo_url) == "dQw4w9WgXcQ"
    assert extract_playlist_id(combo_url) == "PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert is_valid_youtube_url(combo_url) is True
    assert is_valid_youtube_playlist_url(combo_url) is False  # Treated as video primarily

    # 4. Invalid URLs
    assert is_valid_youtube_url("https://example.com/not-yt") is False
    assert is_valid_youtube_playlist_url("https://example.com/not-yt") is False


@pytest.mark.asyncio
async def test_youtube_playlist_metadata_extraction():
    yt_service = YouTubeService()
    test_pl_url = "https://www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    pl_info = await yt_service.get_playlist_info(test_pl_url)

    assert pl_info["playlist_id"] == "PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
    assert "Pop Music" in pl_info["title"] or len(pl_info["title"]) > 0
    assert pl_info["total_videos"] > 10
    assert pl_info["thumbnail"] is not None
    assert len(pl_info["videos"]) > 10

    first_video = pl_info["videos"][0]
    assert first_video["id"] is not None
    assert len(first_video["title"]) > 0
    assert first_video["thumbnail"] is not None
    assert "youtube.com/watch?v=" in first_video["url"]
    assert first_video["index"] == 1


@pytest.mark.asyncio
async def test_api_info_playlist_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_pl_url = "https://www.youtube.com/playlist?list=PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
        response = await client.post("/api/info", json={"url": test_pl_url})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["platform"] == "youtube"
        assert data["entity_type"] == "playlist"
        assert data["playlist_id"] == "PLMC9KNkIncKtPzgY-5rmhvj7fax8fdxoj"
        assert data["total_videos"] > 10
        assert len(data["videos"]) > 10
