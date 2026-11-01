from pathlib import Path

import pytest
from fastapi import HTTPException

from app.main import _managed_file_path


def test_managed_file_path_accepts_exact_app_owned_filename(tmp_path: Path):
    storage = tmp_path / "media"
    storage.mkdir()
    expected = "asset-123.mp4"
    path = _managed_file_path(storage, expected, expected)
    assert path == storage.resolve() / expected


@pytest.mark.parametrize(
    ("stored_name", "expected_name"),
    [
        ("../outside.txt", "../outside.txt"),
        ("..\\outside.txt", "..\\outside.txt"),
        ("asset-123.mp4", "different-asset.mp4"),
        ("", "asset-123.mp4"),
        (".", "."),
        ("nested/asset-123.mp4", "nested/asset-123.mp4"),
    ],
)
def test_managed_file_path_rejects_untrusted_names(
    tmp_path: Path, stored_name: str, expected_name: str
):
    storage = tmp_path / "media"
    storage.mkdir()
    with pytest.raises(HTTPException) as error:
        _managed_file_path(storage, stored_name, expected_name)
    assert error.value.status_code == 500


def test_managed_file_path_rejects_symlink_to_outside_storage(tmp_path: Path):
    storage = tmp_path / "media"
    storage.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("must remain private", encoding="utf-8")
    link = storage / "asset-123.mp4"
    link.symlink_to(outside)

    with pytest.raises(HTTPException) as error:
        _managed_file_path(storage, "asset-123.mp4", "asset-123.mp4")

    assert error.value.status_code == 500
    assert outside.read_text(encoding="utf-8") == "must remain private"
