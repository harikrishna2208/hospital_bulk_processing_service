import httpx
import pytest

from app.clients.hospital_directory_client import DownstreamHTTPError, _is_retryable


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(429, True), (500, True), (503, True), (400, False), (404, False), (422, False)],
)
def test_is_retryable_classifies_downstream_http_errors(status_code, expected):
    assert _is_retryable(DownstreamHTTPError(status_code, "detail")) is expected


@pytest.mark.parametrize(
    "exc",
    [httpx.TimeoutException("timeout"), httpx.ConnectError("connect failed")],
)
def test_is_retryable_treats_connection_and_timeout_as_retryable(exc):
    assert _is_retryable(exc) is True


def test_is_retryable_rejects_unrelated_exceptions():
    assert _is_retryable(ValueError("not a downstream concern")) is False


async def test_create_hospital_retries_on_429_then_succeeds(respx_mock, hospital_client):
    call_count = {"n": 0}

    def _side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"id": 1, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_side_effect)

    result = await hospital_client.create_hospital("Hospital", "1 Main St", None, "batch-1")
    assert call_count["n"] == 2
    assert result["id"] == 1


async def test_create_hospital_retries_on_5xx_then_succeeds(respx_mock, hospital_client):
    call_count = {"n": 0}

    def _side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"id": 2, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_side_effect)

    result = await hospital_client.create_hospital("Hospital", "1 Main St", None, "batch-1")
    assert call_count["n"] == 3
    assert result["id"] == 2


async def test_create_hospital_does_not_retry_on_400(respx_mock, hospital_client):
    create_route = respx_mock.post("/hospitals/").mock(
        return_value=httpx.Response(400, json={"detail": "duplicate hospital"})
    )

    with pytest.raises(DownstreamHTTPError) as exc_info:
        await hospital_client.create_hospital("Hospital", "1 Main St", None, "batch-1")

    assert exc_info.value.status_code == 400
    assert create_route.call_count == 1


async def test_create_hospital_retries_on_timeout_then_succeeds(respx_mock, hospital_client):
    call_count = {"n": 0}

    def _side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.TimeoutException("timed out")
        return httpx.Response(200, json={"id": 3, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_side_effect)

    result = await hospital_client.create_hospital("Hospital", "1 Main St", None, "batch-1")
    assert call_count["n"] == 2
    assert result["id"] == 3


async def test_create_hospital_retries_on_connection_error_then_succeeds(respx_mock, hospital_client):
    call_count = {"n": 0}

    def _side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"id": 4, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_side_effect)

    result = await hospital_client.create_hospital("Hospital", "1 Main St", None, "batch-1")
    assert call_count["n"] == 2
    assert result["id"] == 4


async def test_create_hospital_raises_after_exhausting_retries(respx_mock, hospital_client):
    create_route = respx_mock.post("/hospitals/").mock(return_value=httpx.Response(503))

    with pytest.raises(DownstreamHTTPError) as exc_info:
        await hospital_client.create_hospital("Hospital", "1 Main St", None, "batch-1")

    assert exc_info.value.status_code == 503
    assert create_route.call_count == hospital_client._settings.max_retries


async def test_get_batch_translates_404_into_empty_list(respx_mock, hospital_client):
    respx_mock.get(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    result = await hospital_client.get_batch("batch-1")
    assert result == []


async def test_get_batch_propagates_non_404_errors(respx_mock, hospital_client):
    respx_mock.get(url__regex=r"/hospitals/batch/.+$").mock(return_value=httpx.Response(500))

    with pytest.raises(DownstreamHTTPError) as exc_info:
        await hospital_client.get_batch("batch-1")

    assert exc_info.value.status_code == 500


async def test_delete_batch_propagates_404(respx_mock, hospital_client):
    respx_mock.delete(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    with pytest.raises(DownstreamHTTPError) as exc_info:
        await hospital_client.delete_batch("batch-1")

    assert exc_info.value.status_code == 404
