from __future__ import annotations

import httpx


def chat_completion(
    *,
    base_url: str,
    model: str,
    messages: list[dict],
    http_client: httpx.Client | None = None,
    image_base64: str | None = None,
    timeout: float = 120.0,
) -> str:
    """Call the llama-swap OpenAI-compatible /v1/chat/completions endpoint.

    If image_base64 is given, it is attached to the last message as a
    multimodal `image_url` content part alongside its existing text.
    """
    payload_messages = [dict(m) for m in messages]

    if image_base64 is not None and payload_messages:
        last = payload_messages[-1]
        text = last["content"]
        payload_messages[-1] = {
            "role": last["role"],
            "content": [
                {"type": "text", "text": text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                },
            ],
        }

    payload = {"model": model, "messages": payload_messages}

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        response = client.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    finally:
        if owns_client:
            client.close()
