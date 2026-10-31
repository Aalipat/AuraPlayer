"""
Formatting and sanitization helpers.
"""
import re


def format_bytes(size_bytes: int | float | None) -> str:
    if not size_bytes or size_bytes <= 0:
        return "نامشخص"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def format_seconds(seconds: int | float | None) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def sanitize_filename(name: str, max_length: int = 100) -> str:
    clean = re.sub(r'[\\/*?:"<>|]', '', name)
    clean = re.sub(r'\s+', '_', clean).strip('._')
    return clean[:max_length] if clean else "file"
