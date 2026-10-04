import httpx

from app.schemas.hospital import HospitalInput
from app.services import bulk_processing_service


def _make_rows(n: int) -> list[HospitalInput]:
    return [
        HospitalInput(row=i + 1, name=f"Hospital {i + 1}", address=f"{i + 1} Main St", phone=None)
        for i in range(n)
    ]


async def test_all_succeed_activates_batch(respx_mock, hospital_client, repo, settings):
    rows = _make_rows(3)

    respx_mock.post("/hospitals/").mock(
        side_effect=lambda request: httpx.Response(200, json={"id": 1, "active": False})
    )
    activate_route = respx_mock.patch(url__regex=r"/hospitals/batch/.+/activate").mock(
        return_value=httpx.Response(200, json={"activated_count": 3, "message": "ok"})
    )

    job = await repo.create("batch-1", rows)
    await bulk_processing_service.run_batch("batch-1", rows, hospital_client, repo, settings)

    job = await repo.get("batch-1")
    assert job.status == "completed"
    assert job.batch_activated is True
    assert job.cleanup_status == "not_needed"
    assert all(r.status == "created_and_activated" for r in job.sorted_rows())
    assert activate_route.called


async def test_partial_failure_triggers_cleanup_and_no_activation(
    respx_mock, hospital_client, repo, settings
):
    rows = _make_rows(3)

    def _create_side_effect(request):
        body = request.content.decode()
        if "Hospital 2" in body:
            return httpx.Response(400, json={"detail": "duplicate hospital"})
        return httpx.Response(200, json={"id": 1, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_create_side_effect)
    activate_route = respx_mock.patch(url__regex=r"/hospitals/batch/.+/activate")
    delete_route = respx_mock.delete(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(200, json={"deleted_count": 2, "message": "ok"})
    )

    await repo.create("batch-2", rows)
    await bulk_processing_service.run_batch("batch-2", rows, hospital_client, repo, settings)

    job = await repo.get("batch-2")
    assert job.status == "failed"
    assert job.batch_activated is False
    assert job.cleanup_status == "cleaned_up"
    assert not activate_route.called
    assert delete_route.called

    results = {r.row: r for r in job.sorted_rows()}
    assert results[2].status == "failed"
    assert results[1].status == "failed"
    assert results[3].status == "failed"


async def test_activation_failure_attempts_cleanup(respx_mock, hospital_client, repo, settings):
    rows = _make_rows(2)

    respx_mock.post("/hospitals/").mock(return_value=httpx.Response(200, json={"id": 1, "active": False}))
    respx_mock.patch(url__regex=r"/hospitals/batch/.+/activate").mock(return_value=httpx.Response(500))
    delete_route = respx_mock.delete(url__regex=r"/hospitals/batch/.+$").mock(
        return_value=httpx.Response(200, json={"deleted_count": 2, "message": "ok"})
    )

    await repo.create("batch-3", rows)
    await bulk_processing_service.run_batch("batch-3", rows, hospital_client, repo, settings)

    job = await repo.get("batch-3")
    assert job.status == "failed"
    assert job.batch_activated is False
    assert delete_route.called


async def test_transient_failure_retries_then_succeeds(respx_mock, hospital_client, repo, settings):
    rows = _make_rows(1)
    call_count = {"n": 0}

    def _side_effect(request):
        call_count["n"] += 1
        if call_count["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"id": 42, "active": False})

    respx_mock.post("/hospitals/").mock(side_effect=_side_effect)
    respx_mock.patch(url__regex=r"/hospitals/batch/.+/activate").mock(
        return_value=httpx.Response(200, json={"activated_count": 1, "message": "ok"})
    )

    await repo.create("batch-4", rows)
    await bulk_processing_service.run_batch("batch-4", rows, hospital_client, repo, settings)

    job = await repo.get("batch-4")
    assert call_count["n"] == 2
    assert job.status == "completed"
    assert job.sorted_rows()[0].hospital_id == 42
