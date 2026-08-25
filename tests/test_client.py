"""Client tests: auth/request building, error mapping, pagination.

All HTTP is mocked with httpx.MockTransport — no live Instantly API is touched.
"""

from __future__ import annotations

import httpx
import pytest

from instantly_mcp.client import (
    DEFAULT_BASE_URL,
    InstantlyAPIError,
    InstantlyClient,
)


def make_client(handler, key="secret-key-123"):
    return InstantlyClient(api_key=key, transport=httpx.MockTransport(handler))


# --- request building -------------------------------------------------------
async def test_auth_header_and_base_url():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler, key="abc123")
    result = await client._request("GET", "/workspaces/current")
    assert result == {"ok": True}
    assert captured["auth"] == "Bearer abc123"
    assert captured["url"] == f"{DEFAULT_BASE_URL}/workspaces/current"
    await client.aclose()


async def test_none_params_are_dropped():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    client = make_client(handler)
    await client._request("GET", "/campaigns", params={"status": None, "limit": 10})
    assert "status" not in captured["query"]
    assert captured["query"]["limit"] == "10"
    await client.aclose()


async def test_empty_body_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = make_client(handler)
    assert await client._request("POST", "/emails/threads/x/mark-as-read") is None
    await client.aclose()


# --- error mapping ----------------------------------------------------------
@pytest.mark.parametrize("status,hint", [
    (401, "INSTANTLY_API_KEY"),
    (403, "scope"),
    (404, "not found"),
    (422, "validation"),
])
async def test_error_status_hints(status, hint):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "boom"})

    client = make_client(handler)
    with pytest.raises(InstantlyAPIError) as exc:
        await client._request("GET", "/campaigns")
    err = exc.value
    assert err.status == status
    assert err.api_message == "boom"
    assert "boom" in str(err)
    assert hint in str(err)
    await client.aclose()


async def test_error_never_leaks_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    client = make_client(handler, key="super-secret-key")
    with pytest.raises(InstantlyAPIError) as exc:
        await client._request("GET", "/campaigns")
    assert "super-secret-key" not in str(exc.value)
    await client.aclose()


async def test_rate_limit_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "slow down"},
                              headers={"Retry-After": "30"})

    client = make_client(handler)
    with pytest.raises(InstantlyAPIError) as exc:
        await client._request("GET", "/emails")
    assert exc.value.retry_after == "30"
    assert "30" in str(exc.value)
    await client.aclose()


async def test_non_json_error_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = make_client(handler)
    with pytest.raises(InstantlyAPIError) as exc:
        await client._request("GET", "/campaigns")
    assert "Internal Server Error" in str(exc.value)
    await client.aclose()


# --- pagination -------------------------------------------------------------
ALL_ITEMS = [{"id": i} for i in range(1, 6)]  # ids 1..5


def _paged(after, limit):
    start = int(after) if after not in (None, "") else 0
    page = [x for x in ALL_ITEMS if x["id"] > start][:limit]
    nxt = None
    if page and page[-1]["id"] < ALL_ITEMS[-1]["id"]:
        nxt = str(page[-1]["id"])
    return {"items": page, "next_starting_after": nxt}


async def test_paginate_get_follows_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        limit = int(request.url.params.get("limit", 100))
        after = request.url.params.get("starting_after")
        return httpx.Response(200, json=_paged(after, limit))

    client = make_client(handler)
    # per_page becomes min(100, limit)=2, so this walks multiple pages to reach 5.
    items, has_more = await client.paginate("/x", method="GET", limit=100)
    assert [x["id"] for x in items] == [1, 2, 3, 4, 5]
    assert has_more is False
    await client.aclose()


async def test_paginate_respects_limit_and_flags_truncation():
    def handler(request: httpx.Request) -> httpx.Response:
        limit = int(request.url.params.get("limit", 100))
        after = request.url.params.get("starting_after")
        return httpx.Response(200, json=_paged(after, limit))

    client = make_client(handler)
    items, has_more = await client.paginate("/x", method="GET", limit=3)
    assert [x["id"] for x in items] == [1, 2, 3]
    assert has_more is True  # more rows remained
    await client.aclose()


async def test_paginate_post_sends_cursor_in_body():
    seen_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content or b"{}")
        seen_bodies.append(body)
        # Simulate a server whose real page size (2) is smaller than requested,
        # so pagination must follow the cursor across multiple pages.
        after = body.get("starting_after")
        return httpx.Response(200, json=_paged(after, 2))

    client = make_client(handler)
    items, has_more = await client.paginate(
        "/leads/list", method="POST", json={"campaign": "c1"}, limit=100)
    assert [x["id"] for x in items] == [1, 2, 3, 4, 5]
    # Original filter is preserved across pages; cursor is added on later pages.
    assert all(b.get("campaign") == "c1" for b in seen_bodies)
    assert any("starting_after" in b for b in seen_bodies)
    await client.aclose()
