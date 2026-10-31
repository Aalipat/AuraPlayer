"""
Unit tests for AuraPlayer in-app updater and delta patch security.
"""
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.panel.app import app
from src.services.updater import UpdaterService, parse_semver


@pytest.fixture
def client():
    return TestClient(app)


def test_semver_parsing_and_comparison():
    assert parse_semver("v1.0.0") == (1, 0, 0)
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("v2.10.4-beta") == (2, 10, 4)
    assert parse_semver("1.0.1") > parse_semver("1.0.0")
    assert parse_semver("v1.1.0") > parse_semver("v1.0.9")
    assert parse_semver("2.0.0") > parse_semver("1.99.99")


def test_version_info_reader(tmp_path):
    version_file = tmp_path / "version.json"
    version_file.write_text(
        json.dumps({"name": "AuraPlayer", "version": "1.0.5", "repo": "Aalipat/AuraPlayer"}),
        encoding="utf-8",
    )
    service = UpdaterService(base_dir=tmp_path)
    info = service.get_version_info()
    assert info["name"] == "AuraPlayer"
    assert info["version"] == "1.0.5"
    assert info["repo"] == "Aalipat/AuraPlayer"


def test_zipslip_security_prevention(tmp_path):
    """Ensure patch validator blocks path traversal attacks."""
    service = UpdaterService(base_dir=tmp_path)
    malicious_zip = tmp_path / "malicious.zip"

    # Create zip with directory traversal
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../evil.txt", "exploit")

    result = service.apply_patch_file(malicious_zip)
    assert result["success"] is False
    assert "ناامن" in result["message"] or "غیرمجاز" in result["message"]


def test_disallowed_root_files_prevention(tmp_path):
    """Ensure patch validator only allows src/ or version.json to be patched."""
    service = UpdaterService(base_dir=tmp_path)
    bad_zip = tmp_path / "bad.zip"

    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("bin/bad.exe", "fake")

    result = service.apply_patch_file(bad_zip)
    assert result["success"] is False
    assert "مسیر غیرمجاز" in result["message"]


def test_valid_patch_extraction(tmp_path):
    """Ensure valid src/ patch extracts cleanly with backup support."""
    # Setup dummy src
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "test.py").write_text("old_content = 1", encoding="utf-8")

    # Setup valid patch
    valid_zip = tmp_path / "valid_patch.zip"
    with zipfile.ZipFile(valid_zip, "w") as zf:
        zf.writestr("src/test.py", "new_content = 2")
        zf.writestr("version.json", json.dumps({"version": "1.0.1"}))

    service = UpdaterService(base_dir=tmp_path)
    result = service.apply_patch_file(valid_zip)
    assert result["success"] is True

    # Check updated content
    updated_code = (src_dir / "test.py").read_text(encoding="utf-8")
    assert "new_content = 2" in updated_code


def test_api_update_check_endpoint(client):
    res = client.get("/api/system/update-check")
    assert res.status_code == 200
    data = res.json()
    assert "has_update" in data
    assert "current_version" in data
    assert data["current_version"] == "1.0.0"
