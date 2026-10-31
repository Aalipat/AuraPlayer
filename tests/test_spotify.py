"""
Automated unit & integration tests for Spotify Downloader functionality.
"""
import asyncio
from pathlib import Path
import pytest
from mutagen.mp3 import MP3
from mutagen.id3 import ID3

from src.services.spotify import (
    SpotifyService,
    is_valid_spotify_url,
    parse_spotify_url,
    spotify_service,
)
from src.services.media import MediaService


def test_spotify_url_parsing():
    # 1. Track URLs
    url1 = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=abc"
    kind1, id1 = parse_spotify_url(url1)
    assert kind1 == "track"
    assert id1 == "4cOdK2wGLETKBW3PvgPWqT"
    assert is_valid_spotify_url(url1) is True

    # 2. International Track URL
    url2 = "https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT"
    kind2, id2 = parse_spotify_url(url2)
    assert kind2 == "track"
    assert id2 == "4cOdK2wGLETKBW3PvgPWqT"

    # 3. Spotify URI
    uri = "spotify:track:4cOdK2wGLETKBW3PvgPWqT"
    kind3, id3 = parse_spotify_url(uri)
    assert kind3 == "track"
    assert id3 == "4cOdK2wGLETKBW3PvgPWqT"

    # 4. Album & Playlist
    album_url = "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3"
    kind_alb, id_alb = parse_spotify_url(album_url)
    assert kind_alb == "album"
    assert id_alb == "1DFixLWuPkv3KT3TnV35m3"

    playlist_url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
    kind_pl, id_pl = parse_spotify_url(playlist_url)
    assert kind_pl == "playlist"
    assert id_pl == "37i9dQZF1DXcBWIGoYBM5M"

    # 5. Invalid
    assert is_valid_spotify_url("https://example.com/not-spotify") is False


@pytest.mark.asyncio
async def test_spotify_metadata_extraction():
    url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
    meta = await spotify_service.get_metadata(url)
    assert meta["platform"] == "spotify"
    assert meta["entity_type"] == "track"
    assert "Never Gonna Give You Up" in meta["title"]
    assert "Rick Astley" in meta["artist"]
    assert meta["duration_seconds"] > 200
    assert meta["cover"] is not None
    assert "spotifycdn" in meta["cover"] or "scdn" in meta["cover"]


@pytest.mark.asyncio
async def test_spotify_album_extraction():
    url = "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3"
    meta = await spotify_service.get_metadata(url)
    assert meta["platform"] == "spotify"
    assert meta["entity_type"] == "album"
    assert meta["total_tracks"] >= 10
    assert len(meta["tracks"]) >= 10
    first_track = meta["tracks"][0]
    assert "title" in first_track
    assert "artist" in first_track
    assert "duration" in first_track


@pytest.mark.asyncio
async def test_lrclib_and_persian_translation():
    # 1. Fetch lyrics for Rick Astley
    lyrics = await spotify_service.get_lyrics("Never Gonna Give You Up", "Rick Astley", 213)
    assert lyrics["has_lyrics"] is True
    assert lyrics["is_synced"] is True
    synced = lyrics["synced_lyrics"]
    assert "[00:" in synced

    # 2. Translate sample lyrics
    sample_lrc = (
        "[00:18.90] We're no strangers to love\n"
        "[00:22.61] You know the rules and so do I"
    )
    res = await spotify_service.translate_lyrics_to_persian(sample_lrc)
    assert "[00:18.90]" in res["persian_synced"]
    assert "[00:22.61]" in res["persian_synced"]
    # Check Persian characters exist
    assert any('\u0600' <= char <= '\u06FF' for char in res["persian_synced"])
    # Check bilingual contains both
    assert "We're no strangers to love" in res["bilingual"]
    assert "(" in res["bilingual"]


@pytest.mark.asyncio
async def test_media_service_id3_tagging(tmp_path):
    import subprocess
    media = MediaService()
    dummy_mp3 = tmp_path / "test_song.mp3"
    # Generate a real 1-second silent MP3 with ffmpeg
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
        '-t', '1', '-acodec', 'libmp3lame', '-b:a', '128k', str(dummy_mp3)
    ], capture_output=True, check=True)

    await media.tag_mp3(
        mp3_path=dummy_mp3,
        title="Test Title",
        artist="Test Artist",
        album="Test Album",
        year="2026",
        track_num=1,
        lyrics="Test Lyrics Line 1\nLine 2"
    )

    audio = MP3(str(dummy_mp3), ID3=ID3)
    assert audio.tags.get("TIT2").text[0] == "Test Title"
    assert audio.tags.get("TPE1").text[0] == "Test Artist"
    assert audio.tags.get("TALB").text[0] == "Test Album"
    assert str(audio.tags.get("TDRC").text[0]) == "2026"
    assert str(audio.tags.get("TRCK").text[0]) == "1"
    uslt_frames = audio.tags.getall("USLT")
    assert len(uslt_frames) > 0
    assert "Test Lyrics" in uslt_frames[0].text


@pytest.mark.asyncio
async def test_api_spotify_endpoints():
    from httpx import ASGITransport, AsyncClient
    from src.panel.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test 1: /api/info with Spotify track
        res1 = await client.post("/api/info", json={"url": "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"})
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["platform"] == "spotify"
        assert data1["entity_type"] == "track"
        assert "Never Gonna Give You Up" in data1["title"]

        # Test 2: /api/spotify/lyrics
        res2 = await client.post("/api/spotify/lyrics", json={
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley"
        })
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["success"] is True
        assert data2["has_lyrics"] is True
        assert "[00:" in data2["synced_lyrics"]
        assert len(data2["persian_synced"]) > 0

        # Test 3: /api/folder-config and /api/reset-folder
        res3 = await client.get("/api/folder-config")
        assert res3.status_code == 200
        data3 = res3.json()
        assert "current_path" in data3

        res4 = await client.post("/api/reset-folder")
        assert res4.status_code == 200
        data4 = res4.json()
        assert data4["success"] is True

        # Test 4: /api/open-folder
        res5 = await client.post("/api/open-folder", json={})
        assert res5.status_code == 200
        data5 = res5.json()
        assert data5["success"] is True
        assert "path" in data5

        # Test 5: /api/tasks/active and queue management
        res6 = await client.get("/api/tasks/active")
        assert res6.status_code == 200
        data6 = res6.json()
        assert "tasks" in data6
        assert isinstance(data6["tasks"], list)

        # Test 6: /api/tasks/clear-completed
        res7 = await client.post("/api/tasks/clear-completed")
        assert res7.status_code == 200
        data7 = res7.json()
        assert data7["success"] is True


@pytest.mark.asyncio
async def test_individual_track_cover_extraction():
    """Verify that get_track_cover retrieves the exact individual 640x640 artwork."""
    track_id = "4cOdK2wGLETKBW3PvgPWqT"  # Never Gonna Give You Up
    cover = await spotify_service.get_track_cover(track_id)
    assert cover is not None
    assert "0000b273" in cover  # 640x640 HQ identifier
    assert "spotifycdn" in cover or "scdn" in cover

