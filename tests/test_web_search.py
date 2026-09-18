import json

import httpx
import pytest

from app.web_search import web_search


def _client_with_transport(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_web_search_returns_text_from_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "resultado da busca em JSON"}]},
            },
        )

    client = _client_with_transport(handler)
    result = web_search("clima em São Paulo", http_client=client)

    assert result == "resultado da busca em JSON"


def test_web_search_returns_text_from_sse_response():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "resultado da busca em SSE"}]},
            }
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {body}\n\n",
        )

    client = _client_with_transport(handler)
    result = web_search("clima em São Paulo", http_client=client)

    assert result == "resultado da busca em SSE"


def test_web_search_sends_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"result": {"content": [{"text": "ok"}]}},
        )

    client = _client_with_transport(handler)
    web_search("consulta de teste", num_results=3, http_client=client)

    assert captured["json"]["method"] == "tools/call"
    assert captured["json"]["params"]["name"] == "web_search_exa"
    assert captured["json"]["params"]["arguments"]["query"] == "consulta de teste"
    assert captured["json"]["params"]["arguments"]["numResults"] == 3


def test_web_search_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        web_search("consulta", http_client=client)


def test_web_search_raises_value_error_on_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"content": []}})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        web_search("consulta", http_client=client)
