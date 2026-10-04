import httpx
import pytest

from app.config import get_settings
from app.main import app
from app.repositories.batch_repository import batch_repository
from tests.conftest import BASE_URL, make_csv


@pytest.fixture(autouse=True)
def _reset_settings_and_repo(monkeypatch):
    monkeypatch.setenv("HOSPITAL_DIRECTORY_BASE_URL", BASE_URL)
    monkeypatch.setenv("RETRY_BASE_DELAY_SECONDS", "0.01")
    get_settings.cache_clear()
    batch_repository._jobs.clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def api_client():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def test_health_check(api_client):
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_full_success_workflow_activates_batch(api_client, respx_mock):
    respx_mock.post("/hospitals/").mock(
        side_effect=lambda request: httpx.Response(200, json={"id": 1, "active": False})
    )
    respx_mock.patch(url__regex=r"/hospitals/batch/.+/activate").mock(
        return_value=httpx.Response(200, json={"activated_count": 20, "message": "ok"})
    )

    rows = [(f"Hospital {i}", f"{i} Main St", "") for i in range(20)]
    csv_bytes = make_csv(rows)

    response = await api_client.post(
        "/hospitals/bulk", files={"file": ("hospitals.csv", csv_bytes, "text/csv")}
    )
    assert response.status_code == 202
    batch_id = response.json()["batch_id"]

    status_response = await api_client.get(f"/hospitals/bulk/{batch_id}")
    body = status_response.json()
    assert body["status"] == "completed"
    assert body["batch_activated"] is True
    assert body["total_hospitals"] == 20
    assert body["processed_hospitals"] == 20
    assert body["failed_hospitals"] == 0
    assert all(h["status"] == "created_and_activated" for h in body["hospitals"])


async def test_partial_failure_does_not_activate_and_cleans_up(api_client, respx_mock):
    def _create_side_effect(request):
        if "Hospital 5" in request.content.decode():
            return httpx.Response(400, json={"detail": "invalid"})
        return httpx.Response(200, json={"id": 1, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_create_side_effect)
    respx_mock.delete(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(200, json={"deleted_count": 10, "message": "ok"})
    )

    rows = [(f"Hospital {i}", f"{i} Main St", "") for i in range(11)]
    csv_bytes = make_csv(rows)

    response = await api_client.post(
        "/hospitals/bulk", files={"file": ("hospitals.csv", csv_bytes, "text/csv")}
    )
    batch_id = response.json()["batch_id"]

    status_response = await api_client.get(f"/hospitals/bulk/{batch_id}")
    body = status_response.json()
    assert body["status"] == "failed"
    assert body["batch_activated"] is False
    assert body["cleanup_status"] == "cleaned_up"
    assert body["failed_hospitals"] == 11


async def test_oversized_upload_is_rejected(api_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "10")
    get_settings.cache_clear()

    csv_bytes = make_csv([("General Hospital", "123 Main St", "555-1234")])
    response = await api_client.post(
        "/hospitals/bulk", files={"file": ("hospitals.csv", csv_bytes, "text/csv")}
    )
    assert response.status_code == 413
    get_settings.cache_clear()


async def test_more_than_20_rows_is_rejected(api_client, respx_mock):
    rows = [(f"Hospital {i}", f"{i} Main St", "") for i in range(21)]
    csv_bytes = make_csv(rows)

    response = await api_client.post(
        "/hospitals/bulk", files={"file": ("hospitals.csv", csv_bytes, "text/csv")}
    )
    assert response.status_code == 400


async def test_missing_batch_returns_404(api_client):
    response = await api_client.get("/hospitals/bulk/does-not-exist")
    assert response.status_code == 404


async def test_delete_batch_removes_downstream_and_local_state(api_client, respx_mock):
    respx_mock.post("/hospitals/").mock(
        return_value=httpx.Response(200, json={"id": 1, "active": False})
    )
    respx_mock.patch(url__regex=r"/hospitals/batch/.+/activate").mock(
        return_value=httpx.Response(200, json={"activated_count": 1, "message": "ok"})
    )
    delete_route = respx_mock.delete(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(200, json={"deleted_count": 1, "message": "deleted"})
    )

    csv_bytes = make_csv([("Hospital 1", "1 Main St", "")])
    response = await api_client.post(
        "/hospitals/bulk", files={"file": ("hospitals.csv", csv_bytes, "text/csv")}
    )
    batch_id = response.json()["batch_id"]

    delete_response = await api_client.delete(f"/hospitals/bulk/{batch_id}")
    assert delete_response.status_code == 200
    body = delete_response.json()
    assert body["batch_id"] == batch_id
    assert body["deleted_count"] == 1
    assert delete_route.called

    status_response = await api_client.get(f"/hospitals/bulk/{batch_id}")
    assert status_response.status_code == 404


async def test_delete_batch_propagates_downstream_404(api_client, respx_mock):
    respx_mock.delete(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    response = await api_client.delete("/hospitals/bulk/does-not-exist")
    assert response.status_code == 404


async def test_validate_endpoint_reports_errors_without_processing(api_client, respx_mock):
    create_route = respx_mock.post("/hospitals/")
    csv_bytes = b"name,address,phone\n,123 Main St,555-1234\n"

    response = await api_client.post(
        "/hospitals/bulk/validate", files={"file": ("hospitals.csv", csv_bytes, "text/csv")}
    )
    body = response.json()
    assert body["valid"] is False
    assert not create_route.called
