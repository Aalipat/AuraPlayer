"""
Updater Service for AuraPlayer Desktop.
Checks GitHub Releases for updates, downloads lightweight delta patches,
and applies updates seamlessly without losing user data or requiring full re-installs.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from src.bot.config import BASE_DIR

logger = logging.getLogger(__name__)

DEFAULT_REPO = "Aalipat/AuraPlayer"
DEFAULT_VERSION = "1.0.0"


def parse_semver(version_str: str) -> Tuple[int, ...]:
    """Parse a version string (e.g. 'v1.2.3' or '1.0.0') into an integer tuple."""
    nums = re.findall(r"\d+", version_str)
    return tuple(map(int, nums)) if nums else (0, 0, 0)


class UpdaterService:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = Path(base_dir)
        self.version_file = self.base_dir / "version.json"
        self.cache_dir = self.base_dir / "data" / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_version_info(self) -> Dict[str, Any]:
        """Read local version.json file or return defaults."""
        if self.version_file.is_file():
            try:
                with open(self.version_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read version.json: {e}")
        return {
            "name": "AuraPlayer",
            "version": DEFAULT_VERSION,
            "repo": DEFAULT_REPO,
        }

    async def check_for_updates(self) -> Dict[str, Any]:
        """
        Query GitHub Releases API for the latest release.
        Returns update availability, release notes, and patch download info.
        """
        info = self.get_version_info()
        repo = info.get("repo", DEFAULT_REPO)
        current_ver = info.get("version", DEFAULT_VERSION)
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"

        headers = {
            "User-Agent": "AuraPlayer-Desktop-Updater",
            "Accept": "application/vnd.github.v3+json",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                res = await client.get(api_url, headers=headers)
                if res.status_code == 404:
                    return {
                        "has_update": False,
                        "current_version": current_ver,
                        "latest_version": current_ver,
                        "message": "هنوز ریلیز جدیدی در گیت‌هاب منتشر نشده است.",
                    }
                elif res.status_code != 200:
                    return {
                        "has_update": False,
                        "current_version": current_ver,
                        "latest_version": current_ver,
                        "message": f"خطا در ارتباط با گیت‌هاب (کد وضعیت: {res.status_code})",
                    }

                data = res.json()
                tag = data.get("tag_name", "").lstrip("vV")
                latest_tuple = parse_semver(tag)
                current_tuple = parse_semver(current_ver)

                has_update = latest_tuple > current_tuple

                assets = data.get("assets", [])
                patch_asset = None
                for a in assets:
                    name = a.get("name", "").lower()
                    if name == "patch.zip" or (name.startswith("patch") and name.endswith(".zip")):
                        patch_asset = a
                        break

                return {
                    "has_update": has_update,
                    "current_version": current_ver,
                    "latest_version": tag or current_ver,
                    "release_title": data.get("name", tag),
                    "release_notes": data.get("body", ""),
                    "published_at": data.get("published_at", ""),
                    "html_url": data.get("html_url", f"https://github.com/{repo}/releases"),
                    "patch_url": patch_asset.get("browser_download_url") if patch_asset else None,
                    "patch_size": patch_asset.get("size") if patch_asset else None,
                    "message": "نسخه جدید در دسترس است!" if has_update else "شما از آخرین نسخه استفاده می‌کنید.",
                }
        except Exception as e:
            logger.error(f"Error checking GitHub releases: {e}")
            return {
                "has_update": False,
                "current_version": current_ver,
                "latest_version": current_ver,
                "error": str(e),
                "message": "امکان برقراری ارتباط با گیت‌هاب وجود نداشت. اتصال اینترنت خود را بررسی کنید.",
            }

    async def download_and_apply_patch(self, patch_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Download patch.zip from GitHub release and extract over src/ cleanly.
        """
        if not patch_url:
            check = await self.check_for_updates()
            if not check.get("has_update"):
                return {"success": False, "message": "نسخه جدیدتری برای به‌روزرسانی یافت نشد."}
            patch_url = check.get("patch_url")
            if not patch_url:
                return {
                    "success": False,
                    "message": "فایل پچ (patch.zip) در این ریلیز گیت‌هاب پیوست نشده است.",
                }

        patch_file = self.cache_dir / "update_patch.zip"
        try:
            logger.info(f"Downloading update patch from: {patch_url}")
            headers = {"User-Agent": "AuraPlayer-Desktop-Updater"}
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                res = await client.get(patch_url, headers=headers)
                if res.status_code != 200:
                    return {
                        "success": False,
                        "message": f"خطا در دانلود فایل پچ (کد {res.status_code})",
                    }
                with open(patch_file, "wb") as f:
                    f.write(res.content)

            return self.apply_patch_file(patch_file)
        except Exception as e:
            logger.error(f"Failed to download and apply patch: {e}")
            return {"success": False, "message": f"خطا در اعمال به‌روزرسانی: {str(e)}"}

    def apply_patch_file(self, zip_path: Path) -> Dict[str, Any]:
        """
        Safely validate and extract a patch zip file into the application root directory.
        """
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            return {"success": False, "message": "فایل پچ یافت نشد."}

        backup_dir = self.cache_dir / "src_backup"
        src_dir = self.base_dir / "src"

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Security check against ZipSlip (directory traversal)
                for member in zf.namelist():
                    norm_path = os.path.normpath(member)
                    if norm_path.startswith("..") or norm_path.startswith("/") or norm_path.startswith("\\"):
                        return {"success": False, "message": "فایل پچ نامعتبر یا ناامن است."}
                    # Ensure only allowed directories/files are touched
                    first_part = norm_path.split(os.sep)[0]
                    if first_part not in ("src", "version.json"):
                        return {
                            "success": False,
                            "message": f"مسیر غیرمجاز در پچ: {first_part}. پچ فقط باید شامل src یا version.json باشد.",
                        }

                # Safe backup of src
                if src_dir.exists():
                    if backup_dir.exists():
                        shutil.rmtree(backup_dir, ignore_errors=True)
                    shutil.copytree(src_dir, backup_dir)

                # Extract files
                zf.extractall(self.base_dir)

            return {
                "success": True,
                "message": "به‌روزرسانی با موفقیت اعمال شد! برنامه در حال بارگذاری مجدد است...",
            }
        except Exception as e:
            logger.error(f"Error applying patch file: {e}")
            # Restore backup if available
            if backup_dir.exists() and src_dir.exists():
                try:
                    shutil.rmtree(src_dir, ignore_errors=True)
                    shutil.copytree(backup_dir, src_dir)
                    logger.info("Restored backup src after failed patch.")
                except Exception as restore_err:
                    logger.error(f"Failed to restore backup: {restore_err}")

            return {"success": False, "message": f"خطا در جایگزینی فایل‌ها: {str(e)}"}


updater_service = UpdaterService()
