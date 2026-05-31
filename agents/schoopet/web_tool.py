"""Web fetch tool — retrieve page content from a URL for deeper research."""
import html
import re
import logging

logger = logging.getLogger(__name__)

_MAX_BYTES = 50_000
_TIMEOUT_SECONDS = 15

# Tags whose content we drop entirely (scripts, styles, nav boilerplate)
_DROP_TAGS = re.compile(
    r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\n{3,}")


def _html_to_text(raw: str) -> str:
    """Very light HTML → plain text: drop boilerplate tags, strip remaining tags, unescape."""
    raw = _DROP_TAGS.sub(" ", raw)
    raw = _TAG_STRIP.sub(" ", raw)
    raw = html.unescape(raw)
    raw = _WHITESPACE.sub("\n\n", raw)
    return raw.strip()


async def fetch_url(url: str) -> str:
    """Fetch the text content of a web page.

    Retrieves up to 50 KB of text from the URL, strips HTML boilerplate, and
    returns readable plain text. Use this to read event details, restaurant
    descriptions, article text, or any public web page discovered during research.

    Returns an error string if the fetch fails (bad URL, timeout, non-200 status).
    """
    try:
        import httpx
    except ImportError:
        return "Error: httpx is not installed"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SchoopetAgent/1.0)"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        raw = response.content[:_MAX_BYTES].decode("utf-8", errors="replace")

        if "html" in content_type or raw.lstrip().startswith("<"):
            text = _html_to_text(raw)
        else:
            text = raw

        char_count = len(text)
        truncated = len(response.content) > _MAX_BYTES
        suffix = f"\n\n[truncated at {_MAX_BYTES} bytes]" if truncated else ""
        logger.info("fetch_url: url=%s status=%d chars=%d truncated=%s", url, response.status_code, char_count, truncated)
        return text + suffix

    except Exception as exc:
        logger.warning("fetch_url: url=%s error=%s", url, exc)
        return f"Error fetching {url}: {exc}"
