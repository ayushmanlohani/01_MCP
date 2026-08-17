"""Web search, backed by Tavily with ddgs (DuckDuckGo) as an emergency fallback."""

import os
from typing import Literal

import requests
from ddgs import DDGS

# Must be typing_extensions, not typing: on Python < 3.12 pydantic refuses to build
# a schema from a typing.TypedDict, which breaks the SDK's output-schema generation.
from typing_extensions import TypedDict

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_SECONDS = 15
MAX_RESULTS_LIMIT = 10


class SearchResult(TypedDict):
    """A single page matching the query."""

    title: str
    url: str
    snippet: str
    score: float | None


class WebSearchResponse(TypedDict):
    """The results, plus which engine actually produced them."""

    results: list[SearchResult]
    source: Literal["tavily", "ddgs"]
    warning: str | None


def _search_tavily(query: str, max_results: int) -> list[SearchResult]:
    """Search via Tavily.

    Raises RuntimeError on any hard failure — missing key, network trouble,
    timeout, or an HTTP error status — so the caller can fall back.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not set")

    try:
        response = requests.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": max_results},
            timeout=TAVILY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(str(exc) or exc.__class__.__name__) from exc
    except ValueError as exc:  # body came back as something other than JSON
        raise RuntimeError(f"response was not valid JSON: {exc}") from exc

    return [
        SearchResult(
            title=hit.get("title", ""),
            url=hit.get("url", ""),
            snippet=hit.get("content", ""),
            score=hit.get("score"),
        )
        for hit in payload.get("results", [])[:max_results]
    ]


def _search_ddgs(query: str, max_results: int) -> list[SearchResult]:
    """Search via ddgs (unofficial DuckDuckGo).

    Raises RuntimeError on any failure. The catch is deliberately broad: this is
    the last line of defence, and ddgs surfaces errors from its own HTTP layer
    that aren't all DDGSException subclasses.
    """
    try:
        hits = DDGS().text(query, max_results=max_results)
    except Exception as exc:
        raise RuntimeError(str(exc) or exc.__class__.__name__) from exc

    return [
        SearchResult(
            title=hit.get("title", ""),
            url=hit.get("href", ""),
            snippet=hit.get("body", ""),
            # ddgs reports no relevance score, and a fake one would be worse than none.
            score=None,
        )
        for hit in hits[:max_results]
    ]


def web_search(query: str, max_results: int = 5) -> WebSearchResponse:
    """Search the web and return matching pages as title, URL and a short snippet.

    Returns links and snippets only, not full page content.

    Args:
        query: What to search for. Must not be blank.
        max_results: How many results to return. Clamped to 1-10.

    Returns:
        The results, `source` naming the engine that answered ("tavily" or
        "ddgs"), and `warning`, which is set only when the primary engine failed
        and the fallback engine answered in its place.
    """
    if not query.strip():
        raise ValueError("query must not be blank")

    max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))

    try:
        # A Tavily call that succeeds with zero results is trusted as-is: ddgs
        # won't out-search Tavily on a query its index came up empty on.
        results = _search_tavily(query, max_results)
    except RuntimeError as tavily_error:
        try:
            results = _search_ddgs(query, max_results)
        except RuntimeError as ddgs_error:
            raise RuntimeError(
                f"web_search failed: Tavily error ({tavily_error}); "
                f"ddgs fallback error ({ddgs_error})"
            ) from ddgs_error

        return WebSearchResponse(
            results=results,
            source="ddgs",
            warning=f"Tavily failed ({tavily_error}); served by ddgs instead",
        )

    return WebSearchResponse(results=results, source="tavily", warning=None)
