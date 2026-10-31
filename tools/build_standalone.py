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
DIST_DIR = ROOT_DIR.parent / "dist"
ARCHIVE_NAME = f"YouTube_Spotify_Desktop_v2.0.0_Standalone.zip"

EXCLUDE_PATTERNS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "build_out",
    "obj",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pdb",
    ".log",
    ".tmp",
}


def should_include(rel_path: str, path_obj: Path) -> bool:
    # Exclude development files inside src/desktop/app obj/bin/
    parts = rel_path.replace("\\", "/").split("/")
    for p in parts:
        if p in EXCLUDE_PATTERNS:
            return False
    if path_obj.suffix.lower() in EXCLUDE_EXTENSIONS:
        return False
    # Don't include downloaded media files in data/downloads
    if "data/downloads" in rel_path.replace("\\", "/") and path_obj.name not in [".gitkeep"]:
        return False
    if "data/transcripts" in rel_path.replace("\\", "/") and path_obj.name not in [".gitkeep"]:
        return False
    if "data/.cache" in rel_path.replace("\\", "/"):
        return False
    # Don't include C# intermediate build artifacts
    if "src/desktop/app/bin" in rel_path.replace("\\", "/") or "src/desktop/app/obj" in rel_path.replace("\\", "/"):
        return False
    return True


def package_standalone():
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    out_zip = DIST_DIR / ARCHIVE_NAME
    if out_zip.exists():
        out_zip.unlink()

    print(f"[*] Packaging standalone desktop application from:")
    print(f"    {ROOT_DIR}")
    print(f"[*] Target Archive:")
    print(f"    {out_zip}\n")

    # Ensure .gitkeep in data directories
    (ROOT_DIR / "data" / "downloads" / ".gitkeep").touch(exist_ok=True)
    (ROOT_DIR / "data" / "transcripts" / ".gitkeep").touch(exist_ok=True)

    count = 0
    total_bytes = 0

    with zipfile.ZipFile(out_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(ROOT_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_PATTERNS and not d.startswith('.')]
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(ROOT_DIR)
                rel_str = str(rel_path)

                if should_include(rel_str, full_path):
                    # Store inside folder named YouTube_Spotify_Downloader_Desktop
                    archive_path = f"YouTube_Spotify_Downloader_Desktop/{rel_str.replace('\\', '/')}"
                    zf.write(full_path, archive_path)
                    count += 1
                    total_bytes += full_path.stat().st_size

    archive_size_mb = out_zip.stat().st_size / (1024 * 1024)
    raw_size_mb = total_bytes / (1024 * 1024)

    print("\n" + "=" * 66)
    print(f"[SUCCESS] Standalone ZIP created successfully!")
    print(f"Archive:       {out_zip.name}")
    print(f"Total Files:   {count:,}")
    print(f"Raw Size:      {raw_size_mb:.1f} MB")
    print(f"Archive Size:  {archive_size_mb:.1f} MB")
    print(f"Output Path:   {out_zip}")
    print("=" * 66)


if __name__ == "__main__":
    package_standalone()
