"""Integration test: extract PDF from real .eml and verify with pypdf."""
import base64
import email as stdlib_email
import io
from pathlib import Path
from unittest.mock import MagicMock

import pypdf
import pytest

from agents.schoopet.email_tool import EmailTool

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EML_PATH = FIXTURES_DIR / "email.eml"


def _eml_to_gmail_payload(eml_path: Path) -> dict:
    """Parse a raw .eml file and convert its MIME structure to Gmail API JSON payload format.

    Gmail API uses base64url-encoded 'body.data' for inline attachment bytes.
    Large attachments use 'body.attachmentId' (not present here — everything is inline).
    """
    with open(eml_path, "rb") as f:
        msg = stdlib_email.message_from_binary_file(f)

    def _convert_part(part) -> dict:
        """Recursively convert email.message.Message → Gmail API part dict."""
        mime_type = part.get_content_type()
        filename = part.get_filename() or ""

        if part.is_multipart():
            return {
                "mimeType": mime_type,
                "filename": filename,
                "body": {},
                "parts": [_convert_part(sub) for sub in part.get_payload()],
            }

        raw_bytes = part.get_payload(decode=True) or b""
        data = base64.urlsafe_b64encode(raw_bytes).decode()
        return {
            "mimeType": mime_type,
            "filename": filename,
            "body": {"data": data},
        }

    headers = []
    for name in ("From", "Subject", "Date"):
        value = msg.get(name, "")
        if value:
            headers.append({"name": name, "value": value})

    return {
        "mimeType": msg.get_content_type(),
        "headers": headers,
        "parts": [_convert_part(p) for p in msg.get_payload()],
    }


def _make_tool() -> EmailTool:
    tool = EmailTool()
    mock_oauth = MagicMock()
    mock_oauth.get_valid_access_token.return_value = "mock-token"
    tool._oauth_client = mock_oauth
    tool._firestore_client = None
    return tool


# ── Tests ──────────────────────────────────────────────────────────────────


def test_eml_fixture_exists():
    """Fixture file is present."""
    assert EML_PATH.exists(), f"Missing fixture: {EML_PATH}"


def test_extract_pdf_bytes_from_eml():
    """_walk_parts extracts non-None bytes for the PDF attachment."""
    payload = _eml_to_gmail_payload(EML_PATH)
    tool = _make_tool()
    results: list[dict] = []
    tool._walk_parts(payload, "eml-test", MagicMock(), results)

    pdf_results = [r for r in results if r["mime_type"] == "application/pdf"]
    assert len(pdf_results) == 1, f"Expected 1 PDF attachment, got: {results}"

    att = pdf_results[0]
    assert att["filename"] == "MirkoMontanari.pdf"
    assert att["bytes"] is not None, "PDF bytes must not be None"
    assert att["bytes"][:4] == b"%PDF", "Bytes do not start with PDF magic bytes"


def test_extracted_pdf_readable_by_pypdf(tmp_path):
    """Extracted PDF bytes produce a valid, page-containing PDF document."""
    payload = _eml_to_gmail_payload(EML_PATH)
    tool = _make_tool()
    results: list[dict] = []
    tool._walk_parts(payload, "eml-test", MagicMock(), results)

    pdf_att = next(r for r in results if r["mime_type"] == "application/pdf")
    pdf_bytes = pdf_att["bytes"]

    # Save to disk so a regular PDF viewer can open it
    out_path = tmp_path / "MirkoMontanari.pdf"
    out_path.write_bytes(pdf_bytes)

    reader = pypdf.PdfReader(str(out_path))
    assert len(reader.pages) >= 1, "PDF has no pages"

    # Also verify in-memory (no file I/O dependency)
    reader2 = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader2.pages) >= 1

    print(f"\nExtracted PDF: {out_path}")
    print(f"Pages: {len(reader.pages)}")
