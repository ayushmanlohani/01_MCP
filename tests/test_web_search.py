from unittest.mock import MagicMock, patch

import pytest
import requests

from tools.web_search import web_search

TAVILY_PAYLOAD = {
    "results": [
        {
            "title": "Result one",
            "url": "https://example.com/one",
            "content": "First snippet",
            "score": 0.91,
        },
        {
            "title": "Result two",
            "url": "https://example.com/two",
            "content": "Second snippet",
            "score": 0.42,
        },
    ]
}

DDGS_HITS = [
    {
        "title": "Fallback one",
        "href": "https://example.org/one",
        "body": "Fallback snippet",
    }
]


@pytest.fixture(autouse=True)
def tavily_key(monkeypatch):
    """Default every test to a configured key; the one test that needs it gone unsets it."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")


def _tavily_response(payload):
    """A stand-in for the requests.Response that a healthy Tavily call returns."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _ddgs_returning(hits):
    """A stand-in for the DDGS class whose instances return `hits` from .text()."""
    instance = MagicMock()
    instance.text.return_value = hits
    return MagicMock(return_value=instance)


def test_tavily_success_returns_scored_results():
    with patch(
        "tools.web_search.requests.post",
        return_value=_tavily_response(TAVILY_PAYLOAD),
    ) as post, patch("tools.web_search.DDGS") as ddgs:
        response = web_search("python typing")

    assert response["source"] == "tavily"
    assert response["warning"] is None
    assert response["results"] == [
        {
            "title": "Result one",
            "url": "https://example.com/one",
            "snippet": "First snippet",
            "score": 0.91,
        },
        {
            "title": "Result two",
            "url": "https://example.com/two",
            "snippet": "Second snippet",
            "score": 0.42,
        },
    ]
    assert post.call_args.kwargs["json"] == {"query": "python typing", "max_results": 5}
    ddgs.assert_not_called()


def test_empty_tavily_result_is_trusted_without_falling_back():
    with patch(
        "tools.web_search.requests.post",
        return_value=_tavily_response({"results": []}),
    ), patch("tools.web_search.DDGS") as ddgs:
        response = web_search("query with genuinely nothing behind it")

    assert response["source"] == "tavily"
    assert response["results"] == []
    assert response["warning"] is None
    ddgs.assert_not_called()


def test_ddgs_rescues_a_network_failure():
    with patch(
        "tools.web_search.requests.post",
        side_effect=requests.ConnectionError("connection refused"),
    ), patch("tools.web_search.DDGS", _ddgs_returning(DDGS_HITS)):
        response = web_search("python typing")

    assert response["source"] == "ddgs"
    assert response["results"] == [
        {
            "title": "Fallback one",
            "url": "https://example.org/one",
            "snippet": "Fallback snippet",
            "score": None,
        }
    ]
    assert "connection refused" in response["warning"]
    assert "served by ddgs instead" in response["warning"]


def test_ddgs_rescues_a_bad_api_key():
    unauthorized = MagicMock()
    unauthorized.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")

    with patch("tools.web_search.requests.post", return_value=unauthorized), patch(
        "tools.web_search.DDGS", _ddgs_returning(DDGS_HITS)
    ):
        response = web_search("python typing")

    assert response["source"] == "ddgs"
    assert "401 Unauthorized" in response["warning"]


def test_missing_api_key_falls_back_without_any_http_call(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with patch("tools.web_search.requests.post") as post, patch(
        "tools.web_search.DDGS", _ddgs_returning(DDGS_HITS)
    ):
        response = web_search("python typing")

    post.assert_not_called()
    assert response["source"] == "ddgs"
    assert "TAVILY_API_KEY" in response["warning"]


def test_both_backends_failing_raises_one_combined_error():
    failing_ddgs = MagicMock()
    failing_ddgs.return_value.text.side_effect = Exception("ddgs rate limited")

    with patch(
        "tools.web_search.requests.post", side_effect=requests.Timeout("timed out")
    ), patch("tools.web_search.DDGS", failing_ddgs):
        with pytest.raises(RuntimeError) as excinfo:
            web_search("python typing")

    message = str(excinfo.value)
    assert "timed out" in message
    assert "ddgs rate limited" in message


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_blank_query_raises_before_any_network_call(query):
    with patch("tools.web_search.requests.post") as post, patch(
        "tools.web_search.DDGS"
    ) as ddgs:
        with pytest.raises(ValueError):
            web_search(query)

    post.assert_not_called()
    ddgs.assert_not_called()


def test_max_results_is_capped_for_tavily():
    with patch(
        "tools.web_search.requests.post",
        return_value=_tavily_response({"results": []}),
    ) as post:
        web_search("python typing", max_results=50)

    assert post.call_args.kwargs["json"]["max_results"] == 10


def test_max_results_cap_also_applies_to_the_ddgs_fallback():
    ddgs = _ddgs_returning(DDGS_HITS)

    with patch(
        "tools.web_search.requests.post", side_effect=requests.Timeout("timed out")
    ), patch("tools.web_search.DDGS", ddgs):
        web_search("python typing", max_results=50)

    ddgs.return_value.text.assert_called_once_with("python typing", max_results=10)
