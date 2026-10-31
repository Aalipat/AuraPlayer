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


def test_accurate_quality_estimation_distinct_sizes():
    yt_service = YouTubeService()
    formats = [
        {'format_id': '18', 'height': 360, 'vcodec': 'avc1', 'acodec': 'mp4a', 'filesize': 15000000},
        {'format_id': '135', 'height': 480, 'vcodec': 'avc1', 'acodec': 'none', 'filesize': 25000000},
        {'format_id': '22', 'height': 720, 'vcodec': 'avc1', 'acodec': 'mp4a', 'filesize': 45000000},
        {'format_id': '137', 'height': 1080, 'vcodec': 'avc1', 'acodec': 'none', 'filesize': 80000000},
        {'format_id': '140', 'height': None, 'vcodec': 'none', 'acodec': 'mp4a', 'filesize': 5000000},
    ]
    qualities = yt_service._estimate_qualities(formats, duration=180)

    # Every quality tier must have its own accurate size and strictly ascend
    assert qualities['360'] == 15000000
    assert qualities['480'] == 30000000  # 25M video + 5M best audio
    assert qualities['720'] == 45000000
    assert qualities['1080'] == 85000000  # 80M video + 5M best audio
    assert qualities['360'] < qualities['480'] < qualities['720'] < qualities['1080']

    # Test missing quality (e.g. only 720p available)
    formats_720_max = [
        {'format_id': '18', 'height': 360, 'vcodec': 'avc1', 'acodec': 'mp4a', 'filesize': 15000000},
        {'format_id': '22', 'height': 720, 'vcodec': 'avc1', 'acodec': 'mp4a', 'filesize': 45000000},
    ]
    q_720 = yt_service._estimate_qualities(formats_720_max, duration=180)
    assert q_720['360'] == 15000000
    assert q_720['720'] == 45000000
    assert q_720['1080'] is None
    assert q_720['480'] is None


def test_podcast_subtitles_and_vtt_parsing():
    from src.services.lyrics import parse_lrc, parse_subtitles

    # 1. Test long podcast LRC with hours [01:15:30.50]
    podcast_lrc = (
        "[00:05.00]Welcome to the podcast\n"
        "[01:15:30.50]Discussion on deep technical topics\n"
        "[02:00:00]Final thoughts and conclusion"
    )
    parsed_lrc = parse_lrc(podcast_lrc)
    assert len(parsed_lrc) == 3
    assert parsed_lrc[0]["time"] == 5.0
    assert parsed_lrc[0]["text"] == "Welcome to the podcast"
    assert parsed_lrc[1]["time"] == 4530.5  # 1*3600 + 15*60 + 30.5
    assert parsed_lrc[1]["text"] == "Discussion on deep technical topics"
    assert parsed_lrc[2]["time"] == 7200.0  # 2 hours

    # 2. Test WebVTT subtitle parsing
    sample_vtt = (
        "WEBVTT\n\n"
        "00:00:01.500 --> 00:00:04.200\n"
        "Podcast Host: Hello and welcome!\n\n"
        "00:01:25.000 --> 00:01:30.000\n"
        "<c.yellow>Guest:</c> Glad to be here today."
    )
    parsed_vtt = parse_subtitles(sample_vtt)
    assert len(parsed_vtt) == 2
    assert parsed_vtt[0]["time"] == 1.5
    assert "Hello and welcome!" in parsed_vtt[0]["text"]
    assert parsed_vtt[1]["time"] == 85.0  # 1*60 + 25
    assert "Glad to be here today." in parsed_vtt[1]["text"]


@pytest.mark.asyncio
async def test_companion_subtitle_lyrics_integration(tmp_path):
    from src.services.lyrics import lyrics_service
    from src.bot.config import settings

    video_file = settings.download_dir / "test_podcast_video.mp4"
    video_file.write_bytes(b"dummy_mp4_bytes")
    vtt_file = settings.download_dir / "test_podcast_video.vtt"
    vtt_file.write_text(
        "WEBVTT\n\n00:00:10.000 --> 00:00:15.000\nHello podcast listener!\n",
        encoding="utf-8"
    )

    try:
        res = await lyrics_service.get_lyrics(media_path=video_file)
        assert res["has_lyrics"] is True
        assert res["is_synced"] is True
        assert len(res["lines"]) == 1
        assert res["lines"][0]["time"] == 10.0
        assert res["lines"][0]["text"] == "Hello podcast listener!"
    finally:
        if video_file.exists():
            video_file.unlink()
        if vtt_file.exists():
            vtt_file.unlink()


def test_bidirectional_subtitle_translation():
    from src.services.subtitles import _translate_snippets
    Snippet = type('Snippet', (), {})

    # 1. English to Persian
    s1 = Snippet()
    s1.start = 5.0
    s1.text = "Welcome to our technical conversation."
    fa_results = _translate_snippets([s1], target_lang="fa")
    assert len(fa_results) == 1
    assert "[00:05]" in fa_results[0]
    # Verify Persian characters
    assert any('\u0600' <= char <= '\u06FF' for char in fa_results[0])

    # 2. Persian to English
    s2 = Snippet()
    s2.start = 12.5
    s2.text = "سلام به همه شنوندگان گرامی"
    en_results = _translate_snippets([s2], target_lang="en")
    assert len(en_results) == 1
    assert "[00:12]" in en_results[0]
    # Verify English characters and no Persian in translation
    assert "listener" in en_results[0].lower() or "hello" in en_results[0].lower() or "welcome" in en_results[0].lower()


@pytest.mark.asyncio
async def test_media_tagging_no_companion_images(tmp_path):
    from src.services.media import MediaService
    import subprocess
    media = MediaService()

    mp3_file = tmp_path / "song_clean.mp3"
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
        '-t', '1', '-acodec', 'libmp3lame', '-b:a', '128k', str(mp3_file)
    ], capture_output=True, check=True)

    # Fake 1x1 GIF/PNG cover
    cover_png = tmp_path / "cover.png"
    cover_png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

    await media.tag_mp3(
        mp3_path=mp3_file,
        title="Test Track",
        artist="Test Artist",
        cover_image=cover_png
    )

    # Ensure NO song_clean.jpg or song_clean.png was written
    assert not (tmp_path / "song_clean.jpg").exists()
    assert not (tmp_path / "song_clean.png").exists()


