"""Tests for Drive URL normalization helpers."""

import pytest

from agents.schoopet.utils import doc_url, drive_file_url, normalize_drive_id, sheet_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("bare-id_123", "bare-id_123"),
        ("https://docs.google.com/document/d/doc123/edit", "doc123"),
        ("https://docs.google.com/spreadsheets/d/sheet123/edit#gid=0", "sheet123"),
        ("https://docs.google.com/presentation/d/slide123/edit", "slide123"),
        ("https://drive.google.com/file/d/file123/view", "file123"),
        ("https://drive.google.com/drive/folders/folder123", "folder123"),
        ("https://drive.google.com/folders/folder456", "folder456"),
        ("https://drive.google.com/open?id=legacy123", "legacy123"),
        ("", ""),
        ("_default_", "_default_"),
        ("https://example.com/not-a-drive-url", "https://example.com/not-a-drive-url"),
    ],
)
def test_normalize_drive_id(value, expected):
    assert normalize_drive_id(value) == expected


def test_drive_url_helpers():
    assert doc_url("doc123") == "https://docs.google.com/document/d/doc123/edit"
    assert sheet_url("sheet123") == "https://docs.google.com/spreadsheets/d/sheet123/edit"
    assert (
        drive_file_url("doc123", "application/vnd.google-apps.document")
        == "https://docs.google.com/document/d/doc123/edit"
    )
    assert (
        drive_file_url("sheet123", "application/vnd.google-apps.spreadsheet")
        == "https://docs.google.com/spreadsheets/d/sheet123/edit"
    )
    assert (
        drive_file_url("slide123", "application/vnd.google-apps.presentation")
        == "https://docs.google.com/presentation/d/slide123/edit"
    )
    assert (
        drive_file_url("folder123", "application/vnd.google-apps.folder")
        == "https://drive.google.com/drive/folders/folder123"
    )
    assert drive_file_url("file123", "application/pdf") == "https://drive.google.com/file/d/file123/view"
