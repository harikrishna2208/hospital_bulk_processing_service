import logging
from typing import Any

import httpx

from app.config import Settings
from app.utils.retry import retry_async

logger = logging.getLogger(__name__)


class DownstreamHTTPError(Exception):
    """Raised when the downstream Hospital Directory API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"downstream returned {status_code}: {detail}")


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("detail", body))
    except ValueError:
        return response.text[:500]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.TransportError)):
        return True
    if isinstance(exc, DownstreamHTTPError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


class HospitalDirectoryClient:
    """Thin wrapper around the downstream Hospital Directory API's bulk-relevant endpoints."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings):
        self._http = http_client
        self._settings = settings

    async def _request_with_retry(self, log_context: str, method: str, url: str, **kwargs) -> httpx.Response:
        async def _do_request() -> httpx.Response:
            response = await self._http.request(
                method, url, timeout=self._settings.request_timeout_seconds, **kwargs
            )
            if response.status_code >= 400:
                raise DownstreamHTTPError(response.status_code, _extract_detail(response))
            return response

        return await retry_async(
            _do_request,
            max_attempts=self._settings.max_retries,
            base_delay_seconds=self._settings.retry_base_delay_seconds,
            is_retryable=_is_retryable,
            log_context=log_context,
        )

    async def create_hospital(
        self, name: str, address: str, phone: str | None, batch_id: str
    ) -> dict[str, Any]:
        response = await self._request_with_retry(
            f"create_hospital batch={batch_id}",
            "POST",
            "/hospitals/",
            json={
                "name": name,
                "address": address,
                "phone": phone,
                "creation_batch_id": batch_id,
            },
        )
        return response.json()

    async def get_batch(self, batch_id: str) -> list[dict[str, Any]]:
        """Returns the hospitals in a batch, or [] if the downstream API reports none (404)."""
        try:
            response = await self._request_with_retry(
                f"get_batch batch={batch_id}", "GET", f"/hospitals/batch/{batch_id}"
            )
        except DownstreamHTTPError as exc:
            if exc.status_code == 404:
                return []
            raise
        return response.json()

    async def activate_batch(self, batch_id: str) -> dict[str, Any]:
        response = await self._request_with_retry(
            f"activate_batch batch={batch_id}", "PATCH", f"/hospitals/batch/{batch_id}/activate"
        )
        return response.json()

    async def delete_batch(self, batch_id: str) -> dict[str, Any]:
        response = await self._request_with_retry(
            f"delete_batch batch={batch_id}", "DELETE", f"/hospitals/batch/{batch_id}"
        )
        return response.json()

    async def ping(self) -> bool:
        """Checks downstream liveness via its own health check (GET /)."""
        try:
            response = await self._http.get("/", timeout=self._settings.request_timeout_seconds)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
