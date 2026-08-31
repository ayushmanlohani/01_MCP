"""Fetch one public web page and return its readable content."""

import re

import trafilatura
from scrapling.fetchers import Fetcher, StealthyFetcher

# Must be typing_extensions, not typing: on Python < 3.12 pydantic refuses to build
# a schema from a typing.TypedDict, which breaks the SDK's output-schema generation.
from typing_extensions import Literal, TypedDict

PLAIN_TIMEOUT_SECONDS = 5
# Scrapling's browser tier counts its timeout in milliseconds, not seconds.
STEALTH_TIMEOUT_MS = 15_000

# Statuses a site serves when it thinks it is talking to a bot, rather than
# because the page genuinely is not there.
BLOCKED_STATUSES = frozenset({403, 429, 503})

# Phrases that only ever show up on an interstitial bot check, never in the
# body of a real article. Matched against the first slice of the page only,
# so an article *about* Cloudflare doesn't trip them.
CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "enable javascript and cookies to continue",
    "verify you are human",
    "verifying you are human",
    "attention required! | cloudflare",
    "ddos protection by",
    "cf-browser-verification",
    "please turn javascript on and reload the page",
)
CHALLENGE_SCAN_CHARS = 4_000

# Below this, extraction found nothing worth calling an article: either a silent
# block, or a JS-only page whose server-side HTML is an empty shell.
MIN_CONTENT_CHARS = 200

# Ceiling on what goes back to the model: enough for a long article, short
# enough that one fetch can't swamp a conversation.
MAX_CONTENT_CHARS = 15_000

TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class FetchResponse(TypedDict):
    """One page's content, plus which fetcher tier actually served it."""

    url: str
    title: str | None
    content: str
    tier: Literal["plain", "stealth"]
    warning: str | None


def _title_of(html: str) -> str | None:
    """Pull the <title> out of raw HTML, or None if the page has none."""
    match = TITLE_PATTERN.search(html)
    if not match:
        return None
    title = " ".join(match.group(1).split())
    return title or None


def _looks_like_a_challenge(html: str) -> bool:
    """True when the page is an anti-bot interstitial rather than real content."""
    head = html[:CHALLENGE_SCAN_CHARS].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


def _extract(html: str, url: str) -> str:
    """Turn raw HTML into clean Markdown, dropping nav/ads/boilerplate."""
    return (
        trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
        )
        or ""
    )


def _fetch_plain(url: str):
    """Fetch through the cheap HTTP tier.

    Raises RuntimeError on any hard failure, so the caller can escalate.

    The catch is deliberately broad: any failure here — DNS, TLS, timeout,
    a curl-level error — means the same thing to the caller.
    """
    try:
        return Fetcher.get(url, timeout=PLAIN_TIMEOUT_SECONDS)
    except Exception as exc:
        raise RuntimeError(str(exc) or exc.__class__.__name__) from exc


def _fetch_stealth(url: str):
    """Fetch through the stealth browser tier, with its anti-detection knobs on.

    Raises RuntimeError on any failure, same as `_fetch_plain`: by this point
    there is no further tier to fall back to.
    """
    try:
        return StealthyFetcher.fetch(
            url,
            timeout=STEALTH_TIMEOUT_MS,
            solve_cloudflare=True,
            block_webrtc=True,
            hide_canvas=True,
        )
    except Exception as exc:
        raise RuntimeError(str(exc) or exc.__class__.__name__) from exc


def fetch_content(url: str, raw_html: bool = False) -> FetchResponse:
    """Fetch a public web page and return its main content as Markdown.

    Tries a plain HTTP fetch first and escalates to a stealth browser only when
    that comes back blocked, challenged, broken, or suspiciously empty.

    Args:
        url: The page to fetch. Must not be blank.
        raw_html: Return the page's raw HTML instead of extracted Markdown.

    Returns:
        The page `content`, the final `url` after redirects, its `title`, the
        `tier` that served it ("plain" or "stealth"), and a `warning`, set only
        when the fetch escalated or the content was truncated.
    """
    if not url.strip():
        raise ValueError("url must not be blank")

    content = ""

    try:
        page = _fetch_plain(url)
        if page.status in BLOCKED_STATUSES:
            reason = f"plain fetch was blocked (HTTP {page.status})"
        elif _looks_like_a_challenge(page.html_content):
            reason = "plain fetch came back as a bot-challenge page"
        else:
            content = _extract(page.html_content, page.url)
            reason = (
                None
                if len(content) >= MIN_CONTENT_CHARS
                else "plain fetch returned too little content to be the real page"
            )
    except RuntimeError as plain_error:
        reason = f"plain fetch failed ({plain_error})"

    tier: Literal["plain", "stealth"] = "plain"
    warning = None
    if reason is not None:
        tier = "stealth"
        warning = f"{reason}; served by the stealth tier instead"
        try:
            page = _fetch_stealth(url)
        except RuntimeError as stealth_error:
            raise RuntimeError(
                f"fetch_content failed for {url}: {reason}; "
                f"stealth tier error ({stealth_error})"
            ) from stealth_error
        content = _extract(page.html_content, page.url)

    # Extraction ran either way: even a raw_html caller wants the escalation
    # decision made on readable content, not on the size of an empty shell.
    if raw_html:
        content = page.html_content

    if len(content) > MAX_CONTENT_CHARS:
        truncation = (
            f"content truncated to {MAX_CONTENT_CHARS:,} characters "
            f"(page held {len(content):,})"
        )
        content = content[:MAX_CONTENT_CHARS]
        warning = f"{warning}; {truncation}" if warning else truncation

    return FetchResponse(
        url=page.url,
        title=_title_of(page.html_content),
        content=content,
        tier=tier,
        warning=warning,
    )
