"""
Developer Tool: Generate Lightweight Delta Update Patch for AuraPlayer.
Packages only the updated `src/` codebase and `version.json` into a tiny ~2MB zip
ready to be uploaded as a GitHub Release asset (`patch.zip`).
"""
import json
import os
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
VERSION_FILE = ROOT_DIR / "version.json"


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/create_patch.py <version> [release_notes]")
        print("Example: python tools/create_patch.py 1.0.1 'بهینه‌سازی پلیر و رفع باگ لیریکس'")
        sys.exit(1)

    new_version = sys.argv[1].lstrip("vV")
    notes = sys.argv[2] if len(sys.argv) > 2 else "به‌روزرسانی و بهینه‌سازی‌های جدید"

    # 1. Update version.json
    version_data = {"name": "AuraPlayer", "version": "1.0.0", "repo": "Aalipat/AuraPlayer"}
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                version_data = json.load(f)
        except Exception:
            pass

    version_data["version"] = new_version
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(version_data, f, indent=2, ensure_ascii=False)
    print(f"[*] Updated version.json -> {new_version}")

    # 2. Prepare output zip paths
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    patch_zip = DIST_DIR / "patch.zip"
    versioned_patch_zip = DIST_DIR / f"patch_v{new_version}.zip"

    # 3. Create zip containing src/ and version.json
    print("[*] Packaging updated source files into patch.zip...")
    ignored_patterns = {
        "__pycache__", ".pyc", ".pyo", ".pytest_cache", ".git", ".DS_Store",
        "Thumbs.db", "bin", "obj", ".vs", "Publish"
    }

    with zipfile.ZipFile(patch_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Add version.json
        zf.write(VERSION_FILE, arcname="version.json")

        # Add src/
        src_dir = ROOT_DIR / "src"
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ignored_patterns and not d.startswith(".")]
            for file in files:
                if any(file.endswith(ext) for ext in (".pyc", ".pyo", ".pdb", ".exe")):
                    continue
                full_path = Path(root) / file
                arcname = full_path.relative_to(ROOT_DIR)
                zf.write(full_path, arcname=str(arcname).replace("\\", "/"))

    # Also keep a versioned copy
    import shutil
    shutil.copy2(patch_zip, versioned_patch_zip)

    size_mb = patch_zip.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"🎉 پچ به‌روزرسانی نسخه v{new_version} با موفقیت ساخته شد!")
    print(f"📦 حجم فوق‌العاده کم پچ: {size_mb:.2f} مگابایت")
    print(f"📁 مسیر فایل: {patch_zip}")
    print("=" * 60)
    print("\n🚀 مراحل انتشار آپدیت در گیت‌هاب:")
    print(f"1. وارد صفحه ریلیزهای گیت‌هاب شوید:")
    print(f"   https://github.com/{version_data.get('repo', 'Aalipat/AuraPlayer')}/releases/new")
    print(f"2. در فیلد Tag version بنویسید: v{new_version}")
    print(f"3. در فیلد Release title بنویسید: AuraPlayer v{new_version}")
    print(f"4. در توضیحات بنویسید: {notes}")
    print(f"5. فایل 'dist/patch.zip' را با ماوس بکشید و در بخش Attach binaries رها کنید.")
    print("6. روی دکمه 'Publish release' کلیک کنید.")
    print("\n✅ تمام! اکنون هر کاربری در برنامه دکمه «بررسی به‌روزرسانی» را بزند،")
    print("این پچ ۲ مگابایتی را خودکار دانلود و جایگزین می‌کند!\n")


if __name__ == "__main__":
    main()
