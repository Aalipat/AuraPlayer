"""
Automated GitHub Release Publisher for AuraPlayer.
Uploads patch.zip (and optionally portable zip) to GitHub Releases.
"""
import os
import sys
import json
from pathlib import Path
import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
VERSION_FILE = ROOT_DIR / "version.json"


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token and len(sys.argv) > 1 and sys.argv[1].startswith("ghp_"):
        token = sys.argv.pop(1)

    if not token:
        print("[!] GitHub token required via GITHUB_TOKEN environment variable.")
        sys.exit(1)

    # Read current version from version.json
    current_ver = "1.0.1"
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                current_ver = data.get("version", current_ver)
        except Exception:
            pass

    tag = f"v{current_ver}" if len(sys.argv) < 2 else sys.argv[1]
    title = f"AuraPlayer {tag} ⚡" if len(sys.argv) < 3 else sys.argv[2]
    default_desc = (
        f"## 🎵 AuraPlayer {tag}\n\n"
        "✨ تغییرات این نسخه:\n"
        "- اتصال به موتور چندگانه لیریکس (NetEase, Megalobiz, Musixmatch) و رفع قطعی سرور LRCLIB.\n"
        "- ذخیره خودکار فایل‌های لیریکس (.lrc) و ترجمه فارسی هماهنگ با زمان (_persian.lrc) هنگام دانلود آهنگ‌ها و آلبوم‌ها.\n"
        "- دکمه اختصاصی دانلود فایل لیریکس (.lrc) روی کارت آهنگ و در لیست قطعات آلبوم/پلی‌لیست.\n"
        "- فیلتر و حذف فایل‌های .gitkeep از خروجی‌های پرتابل.\n\n"
        "### 📦 نحوه به‌روزرسانی:\n"
        "کاربران می‌توانند با ۱ کلیک روی دکمه «نسخه» در بالای برنامه، پچ کم‌حجم (۱.۲۴ مگابایت) را خودکار دریافت کنند."
    )
    description = sys.argv[3] if len(sys.argv) > 3 else default_desc

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "AuraPlayer-Uploader",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # 1. Create Release on GitHub
    print(f"[*] Creating release {tag} on GitHub...")
    rel_url = "https://api.github.com/repos/Aalipat/AuraPlayer/releases"
    payload = {
        "tag_name": tag,
        "target_commitish": "main",
        "name": title,
        "body": description,
        "draft": False,
        "prerelease": False,
    }

    with httpx.Client(timeout=30.0) as client:
        res = client.post(rel_url, json=payload, headers=headers)
        if res.status_code == 422:
            print(f"[*] Release {tag} already exists, fetching info...")
            get_res = client.get(f"{rel_url}/tags/{tag}", headers=headers)
            rel_data = get_res.json()
        elif res.status_code not in (200, 201):
            print(f"[!] Failed to create release: {res.status_code} - {res.text}")
            sys.exit(1)
        else:
            rel_data = res.json()

    release_id = rel_data["id"]
    upload_url_base = f"https://uploads.github.com/repos/Aalipat/AuraPlayer/releases/{release_id}/assets"
    print(f"[*] Release ready! ID: {release_id}")

    # 2. Upload patch.zip
    patch_file = DIST_DIR / "patch.zip"
    if patch_file.exists():
        size_mb = patch_file.stat().st_size / (1024 * 1024)
        print(f"[*] Uploading patch.zip ({size_mb:.2f} MB)...")
        with open(patch_file, "rb") as f:
            data = f.read()

        up_headers = dict(headers)
        up_headers["Content-Type"] = "application/zip"
        up_headers["Content-Length"] = str(len(data))

        with httpx.Client(timeout=60.0) as client:
            res = client.post(f"{upload_url_base}?name=patch.zip", content=data, headers=up_headers)
            if res.status_code in (200, 201):
                print("[SUCCESS] patch.zip uploaded to release!")
            else:
                print(f"[!] Asset response: {res.status_code} - {res.text}")

    print("\n" + "=" * 60)
    print(f"🎉 انتشار ریلیز {tag} با موفقیت انجام شد!")
    print(f"🔗 آدرس صفحه ریلیز جدید:")
    print(f"   https://github.com/Aalipat/AuraPlayer/releases/tag/{tag}")
    print("=" * 60)


if __name__ == "__main__":
    main()
