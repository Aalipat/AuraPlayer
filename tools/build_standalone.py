"""
Automated Standalone Packaging Script for YouTube & Spotify Downloader Desktop
Packages the standalone project into a clean distribution ZIP archive.
"""
import os
import sys
import zipfile
import shutil
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
ARCHIVE_NAME = "YouTube_Spotify_Desktop_v2.0.0_Standalone.zip"

EXCLUDE_PATTERNS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "build_out",
    "obj",
    "dist",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pdb",
    ".log",
    ".tmp",
}


def should_include(rel_path: str, path_obj: Path, is_lite: bool = False) -> bool:
    norm = rel_path.replace("\\", "/")
    parts = norm.split("/")
    for p in parts:
        if p in EXCLUDE_PATTERNS and norm != "bin" and not norm.startswith("bin/"):
            return False
    if path_obj.suffix.lower() in EXCLUDE_EXTENSIONS:
        return False
    if is_lite and (norm == "bin" or norm.startswith("bin/")):
        return False
    # Exclude developer tests
    if norm == "tests" or norm.startswith("tests/"):
        return False
    # Exclude any .gitkeep or hidden files in data
    if path_obj.name == ".gitkeep" or path_obj.name.startswith("."):
        return False
    # Exclude C# source code and build artifacts (already compiled into YouTube_Downloader.exe)
    if norm.startswith("src/desktop"):
        return False
    # Don't include downloaded media files or transcripts
    if norm.startswith("data/downloads") or norm.startswith("data/transcripts"):
        return False
    if norm.startswith("data/.cache"):
        return False
    # Don't include personal session/settings
    if norm in ["data/user_settings.json", "data/queue.json", "data/server.log"]:
        return False
    # Handle .env separately (we will write a clean template)
    if norm == ".env":
        return False
    return True


def package_standalone(is_lite: bool = False):
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = "AuraPlayer_Lite.zip" if is_lite else "AuraPlayer_v1.0.0_Portable.zip"
    out_zip = DIST_DIR / archive_name
    if out_zip.exists():
        out_zip.unlink()

    mode_label = "Lite (No FFmpeg binaries)" if is_lite else "Full Standalone (Offline / All-inclusive)"
    print(f"[*] Mode: {mode_label}")
    print(f"[*] Packaging standalone desktop application from:")
    print(f"    {ROOT_DIR}")
    print(f"[*] Target Archive:")
    print(f"    {out_zip}\n")

    count = 0
    total_bytes = 0

    with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_PATTERNS and not d.startswith('.')]
            if is_lite and "bin" in dirs and root == str(ROOT_DIR):
                dirs.remove("bin")
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(ROOT_DIR)
                rel_str = str(rel_path)

                if should_include(rel_str, full_path, is_lite=is_lite):
                    # Store inside folder named AuraPlayer
                    archive_path = f"AuraPlayer/{rel_str.replace('\\', '/')}"
                    zf.write(full_path, archive_path)
                    count += 1
                    total_bytes += full_path.stat().st_size

        # Add clean relative .env template
        clean_env = (
            "# AuraPlayer Desktop Configuration\n"
            "BOT_TOKEN=\n"
            "PROXY_URL=auto\n"
            "PROXY_FALLBACK_URL=\n"
            "DOWNLOAD_DIR=data/downloads\n"
            "TRANSCRIPT_DIR=data/transcripts\n"
            "MAX_FILE_SIZE_MB=48\n"
        )
        zf.writestr("AuraPlayer/.env", clean_env)
        zf.writestr("AuraPlayer/data/downloads/", "")
        zf.writestr("AuraPlayer/data/transcripts/", "")
        count += 3

    archive_size_mb = out_zip.stat().st_size / (1024 * 1024)
    raw_size_mb = total_bytes / (1024 * 1024)

    print("\n" + "=" * 66)
    print(f"[SUCCESS] Standalone ZIP created successfully!")
    print(f"Archive:       {out_zip.name}")
    print(f"Mode:          {mode_label}")
    print(f"Total Files:   {count:,}")
    print(f"Extracted Size:{raw_size_mb:.1f} MB")
    print(f"Zip File Size: {archive_size_mb:.1f} MB")
    print(f"Output Path:   {out_zip}")
    print("=" * 66)


if __name__ == "__main__":
    is_lite_mode = "--lite" in sys.argv
    package_standalone(is_lite=is_lite_mode)
