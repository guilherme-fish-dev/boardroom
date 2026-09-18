from __future__ import annotations

import json

import httpx

EXA_MCP_URL = "https://mcp.exa.ai/mcp"


def _parse_mcp_response(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise ValueError(f"resposta SSE sem linha 'data:': {response.text}")
    return response.json()


def web_search(
    query: str,
    *,
    num_results: int = 5,
    http_client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> str:
    """Search the web via Exa's public MCP endpoint (no API key required).

    Raises:
        httpx.ConnectError: if the endpoint is unreachable.
        httpx.TimeoutException: if the request times out.
        httpx.HTTPStatusError: if the endpoint responds with an error status.
        ValueError: if the response body doesn't have the expected shape.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa",
            "arguments": {"query": query, "numResults": num_results},
        },
    }

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout)
    try:
        response = client.post(
            EXA_MCP_URL,
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = _parse_mcp_response(response)
        try:
            return data["result"]["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"resposta inesperada da busca: {data}") from exc
    finally:
        if owns_client:
            client.close()
