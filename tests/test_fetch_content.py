from unittest.mock import MagicMock, patch

import pytest

from tools.fetch_content import fetch_content

ARTICLE_HTML = (
    "<html><head><title>A Real Article</title></head>"
    "<body><nav>menu</nav><article><p>The body of the article.</p></article></body></html>"
)

# Long enough to read as a genuine article body rather than a stub - a real
# extraction that came back this short is itself a signal the fetch went wrong.
ARTICLE_MARKDOWN = (
    "# A Real Article\n\n"
    "The body of the article, cleaned up into markdown by the extractor. "
    "It runs on for a few sentences the way a real page would, so that nothing "
    "here trips the too-little-content check that exists to catch empty shells. "
    "It closes with a final paragraph and nothing else.\n"
)


def _page(html=ARTICLE_HTML, status=200, url="https://example.com/article"):
    """A stand-in for the Response object a Scrapling fetcher returns."""
    page = MagicMock()
    page.status = status
    page.html_content = html
    page.url = url
    return page


def test_plain_tier_serves_extracted_markdown():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch(
        "tools.fetch_content.trafilatura.extract", return_value=ARTICLE_MARKDOWN
    ):
        plain.get.return_value = _page()
        response = fetch_content("https://example.com/article")

    assert response == {
        "url": "https://example.com/article",
        "title": "A Real Article",
        "content": ARTICLE_MARKDOWN,
        "tier": "plain",
        "warning": None,
    }
    stealth.fetch.assert_not_called()


def test_a_blocked_plain_fetch_escalates_to_the_stealth_tier():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch(
        "tools.fetch_content.trafilatura.extract", return_value=ARTICLE_MARKDOWN
    ):
        plain.get.return_value = _page(html="<html>Access denied</html>", status=403)
        stealth.fetch.return_value = _page()
        response = fetch_content("https://example.com/article")

    assert response["tier"] == "stealth"
    assert response["content"] == ARTICLE_MARKDOWN
    assert response["title"] == "A Real Article"
    assert "403" in response["warning"]
    stealth.fetch.assert_called_once()


def test_a_plain_fetch_that_errors_escalates_to_the_stealth_tier():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch(
        "tools.fetch_content.trafilatura.extract", return_value=ARTICLE_MARKDOWN
    ):
        plain.get.side_effect = TimeoutError("timed out after 5s")
        stealth.fetch.return_value = _page()
        response = fetch_content("https://example.com/article")

    assert response["tier"] == "stealth"
    assert response["content"] == ARTICLE_MARKDOWN
    assert "timed out after 5s" in response["warning"]


def test_a_challenge_page_escalates_even_though_it_returned_200():
    challenge = (
        "<html><head><title>Just a moment...</title></head><body>"
        "<p>Checking your browser before accessing example.com.</p>"
        "<p>This process is automatic. You will be redirected shortly.</p>"
        "</body></html>"
    )

    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch(
        "tools.fetch_content.trafilatura.extract", return_value=ARTICLE_MARKDOWN
    ):
        plain.get.return_value = _page(html=challenge, status=200)
        stealth.fetch.return_value = _page()
        response = fetch_content("https://example.com/article")

    assert response["tier"] == "stealth"
    assert response["title"] == "A Real Article"
    assert "challenge" in response["warning"]


def test_a_javascript_shell_escalates_because_extraction_came_back_empty():
    shell = '<html><head><title>App</title></head><body><div id="root"></div></body></html>'

    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch(
        "tools.fetch_content.trafilatura.extract",
        side_effect=["", ARTICLE_MARKDOWN],
    ):
        plain.get.return_value = _page(html=shell, status=200)
        stealth.fetch.return_value = _page()
        response = fetch_content("https://example.com/article")

    assert response["tier"] == "stealth"
    assert response["content"] == ARTICLE_MARKDOWN
    assert response["title"] == "A Real Article"
    assert "too little content" in response["warning"]


def test_both_tiers_failing_raises_one_combined_error():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch("tools.fetch_content.trafilatura.extract"):
        plain.get.side_effect = TimeoutError("timed out after 5s")
        stealth.fetch.side_effect = Exception("browser launch failed")

        with pytest.raises(RuntimeError) as excinfo:
            fetch_content("https://example.com/article")

    message = str(excinfo.value)
    assert "timed out after 5s" in message
    assert "browser launch failed" in message


def test_an_overlong_page_is_capped_and_says_so():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ), patch("tools.fetch_content.trafilatura.extract", return_value="word " * 6000):
        plain.get.return_value = _page()
        response = fetch_content("https://example.com/article")

    assert response["tier"] == "plain"
    assert len(response["content"]) == 15_000
    assert "truncated" in response["warning"]
    assert "15,000" in response["warning"]


def test_raw_html_returns_page_source_instead_of_markdown():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ), patch("tools.fetch_content.trafilatura.extract", return_value=ARTICLE_MARKDOWN):
        plain.get.return_value = _page()
        response = fetch_content("https://example.com/article", raw_html=True)

    assert response["content"] == ARTICLE_HTML
    assert response["tier"] == "plain"
    assert response["warning"] is None


@pytest.mark.parametrize("url", ["", "   ", "\t\n"])
def test_blank_url_raises_before_any_fetch(url):
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth:
        with pytest.raises(ValueError):
            fetch_content(url)

    plain.get.assert_not_called()
    stealth.fetch.assert_not_called()


def test_each_tier_is_called_with_its_own_timeout_and_stealth_options():
    with patch("tools.fetch_content.Fetcher") as plain, patch(
        "tools.fetch_content.StealthyFetcher"
    ) as stealth, patch(
        "tools.fetch_content.trafilatura.extract", return_value=ARTICLE_MARKDOWN
    ):
        plain.get.return_value = _page(status=403)
        stealth.fetch.return_value = _page()
        fetch_content("https://example.com/article")

    assert plain.get.call_args.kwargs["timeout"] == 5

    stealth_options = stealth.fetch.call_args.kwargs
    assert stealth_options["timeout"] == 15_000  # this tier counts milliseconds
    assert stealth_options["solve_cloudflare"] is True
    assert stealth_options["block_webrtc"] is True
    assert stealth_options["hide_canvas"] is True
