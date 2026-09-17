import httpx
import pytest

from app.llm_client import chat_completion


def _client_with_transport(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_chat_completion_text_only_sends_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = httpx.Request.read(request) and __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "resposta do modelo"}}]},
        )

    client = _client_with_transport(handler)
    result = chat_completion(
        base_url="http://localhost:8080",
        model="qwen2.5-7b",
        messages=[{"role": "system", "content": "persona"}, {"role": "user", "content": "oi"}],
        http_client=client,
    )

    assert result == "resposta do modelo"
    assert captured["json"]["model"] == "qwen2.5-7b"
    assert captured["json"]["messages"][1]["content"] == "oi"


def test_chat_completion_with_image_builds_multimodal_content():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "vejo um gato"}}]},
        )

    client = _client_with_transport(handler)
    result = chat_completion(
        base_url="http://localhost:8080",
        model="llava-7b",
        messages=[{"role": "user", "content": "o que tem na imagem?"}],
        http_client=client,
        image_base64="ZmFrZS1pbWFnZS1ieXRlcw==",
    )

    assert result == "vejo um gato"
    last_message = captured["json"]["messages"][-1]
    assert last_message["role"] == "user"
    content_types = [part["type"] for part in last_message["content"]]
    assert content_types == ["text", "image_url"]
    assert "ZmFrZS1pbWFnZS1ieXRlcw==" in last_message["content"][1]["image_url"]["url"]


def test_chat_completion_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        chat_completion(
            base_url="http://localhost:8080",
            model="qwen2.5-7b",
            messages=[{"role": "user", "content": "oi"}],
            http_client=client,
        )


def test_chat_completion_raises_value_error_on_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        chat_completion(
            base_url="http://localhost:8080",
            model="qwen2.5-7b",
            messages=[{"role": "user", "content": "oi"}],
            http_client=client,
        )


from app.llm_client import list_models


def test_list_models_returns_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "qwen2.5-7b"}, {"id": "llava-7b"}]},
        )

    client = _client_with_transport(handler)
    result = list_models(base_url="http://localhost:8080", http_client=client)

    assert result == ["qwen2.5-7b", "llava-7b"]


def test_list_models_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        list_models(base_url="http://localhost:8080", http_client=client)


def test_list_models_raises_value_error_on_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not_data": []})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        list_models(base_url="http://localhost:8080", http_client=client)


def test_list_models_raises_value_error_when_item_has_no_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"not_id": "x"}]})

    client = _client_with_transport(handler)
    with pytest.raises(ValueError):
        list_models(base_url="http://localhost:8080", http_client=client)
