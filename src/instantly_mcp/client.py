"""Thin async client for the Instantly.ai v2 API.

All HTTP traffic funnels through :meth:`InstantlyClient._request`, which handles
auth headers, timeouts, JSON, and turns non-2xx responses into a clean,
human-readable :class:`InstantlyAPIError` (the raw API key is never included in
any error text). Cursor pagination (``starting_after`` / ``next_starting_after``)
is centralized in :meth:`InstantlyClient.paginate`.

Verified against the live v2 reference at https://developer.instantly.ai/ .
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "https://api.instantly.ai/api/v2"
DEFAULT_TIMEOUT = 30.0
# Instantly caps list endpoints at 100 items/page.
MAX_PAGE_SIZE = 100
# Safety ceiling so a "list" call can never walk the entire workspace.
DEFAULT_PAGE_CAP = 20  # up to DEFAULT_PAGE_CAP * MAX_PAGE_SIZE rows


class InstantlyAPIError(Exception):
    """A clean, human-readable Instantly API error.

    Names the endpoint, HTTP status, and the API's own error message. Never
    contains the API key.
    """

    def __init__(self, method: str, path: str, status: int, message: str,
                 retry_after: Optional[str] = None):
        self.method = method
        self.path = path
        self.status = status
        self.api_message = message
        self.retry_after = retry_after
        hint = {
            401: " (check INSTANTLY_API_KEY — it may be invalid or expired)",
            403: " (the API key's scope/plan can't reach this endpoint)",
            404: " (resource not found)",
            422: " (request validation failed)",
            429: " (rate limited)",
        }.get(status, "")
        if status == 429 and retry_after:
            hint = f" (rate limited — retry after {retry_after}s)"
        super().__init__(
            f"Instantly API {status} on {method} {path}: {message}{hint}"
        )


class InstantlyClient:
    """Async wrapper around the Instantly v2 REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_env(cls, *, transport: Optional[httpx.AsyncBaseTransport] = None
                 ) -> "InstantlyClient":
        api_key = os.environ.get("INSTANTLY_API_KEY", "")
        base_url = os.environ.get("INSTANTLY_BASE_URL", DEFAULT_BASE_URL)
        return cls(api_key=api_key, base_url=base_url, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
    ) -> Any:
        """Perform one request and return decoded JSON (or ``None`` for empty).

        Non-2xx responses raise :class:`InstantlyAPIError`. ``params`` values
        that are ``None`` are dropped so callers can pass optional filters freely.
        """
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        try:
            resp = await self._client.request(
                method.upper(), path, params=params or None, json=json
            )
        except httpx.TimeoutException as exc:
            raise InstantlyAPIError(
                method, path, 0, f"request timed out after {DEFAULT_TIMEOUT}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise InstantlyAPIError(method, path, 0, f"connection error: {exc}") \
                from exc

        if resp.is_success:
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text

        # Extract the API's own message without leaking anything sensitive.
        message = _error_message(resp)
        retry_after = resp.headers.get("Retry-After")
        raise InstantlyAPIError(method, path, resp.status_code, message, retry_after)

    async def paginate(
        self,
        path: str,
        *,
        method: str = "GET",
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        limit: Optional[int] = None,
        page_cap: int = DEFAULT_PAGE_CAP,
    ) -> tuple[list, bool]:
        """Walk cursor pagination and return ``(items, has_more)``.

        ``has_more`` is True when the caller's ``limit`` (or the page cap) stopped
        us before the API ran out of pages — surface it so callers can warn about
        truncation. For GET endpoints the cursor rides in the query string; for
        POST list endpoints (e.g. ``/leads/list``) it rides in the JSON body.
        """
        per_page = MAX_PAGE_SIZE
        if limit is not None:
            per_page = max(1, min(MAX_PAGE_SIZE, limit))

        items: list = []
        cursor: Optional[str] = None
        pages = 0
        while True:
            if method.upper() == "GET":
                page_params = dict(params or {})
                page_params["limit"] = per_page
                if cursor:
                    page_params["starting_after"] = cursor
                data = await self._request("GET", path, params=page_params)
            else:
                body = dict(json or {})
                body["limit"] = per_page
                if cursor:
                    body["starting_after"] = cursor
                data = await self._request(method, path, json=body)

            batch = data.get("items", []) if isinstance(data, dict) else (data or [])
            items.extend(batch)
            cursor = data.get("next_starting_after") if isinstance(data, dict) else None
            pages += 1

            if limit is not None and len(items) >= limit:
                items = items[:limit]
                return items, bool(cursor)
            if not cursor or not batch:
                return items, False
            if pages >= page_cap:
                return items, True


def _error_message(resp: httpx.Response) -> str:
    """Pull a readable message out of an error response body."""
    try:
        body = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return text[:300] if text else resp.reason_phrase or "unknown error"
    if isinstance(body, dict):
        for key in ("message", "error", "detail", "error_message"):
            val = body.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict) and isinstance(val.get("message"), str):
                return val["message"]
        # Validation errors sometimes arrive as a list under "errors".
        if isinstance(body.get("errors"), list) and body["errors"]:
            return "; ".join(str(e) for e in body["errors"])[:300]
        return str(body)[:300]
    return str(body)[:300]
