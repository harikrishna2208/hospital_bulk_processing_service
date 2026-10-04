import asyncio
import logging
import uuid
from typing import Literal

from app.clients.hospital_directory_client import DownstreamHTTPError, HospitalDirectoryClient
from app.config import Settings
from app.repositories.batch_repository import BatchJob, BatchRepository
from app.schemas.hospital import HospitalInput, HospitalRowResult

logger = logging.getLogger(__name__)

CleanupStatus = Literal["not_needed", "cleaned_up", "cleanup_failed"]


async def create_batch_job(repo: BatchRepository, rows: list[HospitalInput]) -> BatchJob:
    batch_id = str(uuid.uuid4())
    return await repo.create(batch_id, rows)


async def run_batch(
    batch_id: str,
    rows: list[HospitalInput],
    client: HospitalDirectoryClient,
    repo: BatchRepository,
    settings: Settings,
) -> None:
    """Creates the given rows downstream (bounded concurrency), then activates or cleans up."""
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    await asyncio.gather(*[_create_one(row, batch_id, client, repo, semaphore) for row in rows])
    await _finalize(batch_id, client, repo)


async def _create_one(
    row: HospitalInput,
    batch_id: str,
    client: HospitalDirectoryClient,
    repo: BatchRepository,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        try:
            hospital = await client.create_hospital(row.name, row.address, row.phone, batch_id)
            result = HospitalRowResult(
                row=row.row, hospital_id=hospital.get("id"), name=row.name, status="created"
            )
        except DownstreamHTTPError as exc:
            result = HospitalRowResult(
                row=row.row,
                name=row.name,
                status="failed",
                error=f"downstream returned {exc.status_code}: {exc.detail}",
            )
        except Exception as exc:  # connection errors, timeouts exhausted, etc.
            logger.exception("batch=%s row=%d unexpected error creating hospital", batch_id, row.row)
            result = HospitalRowResult(row=row.row, name=row.name, status="failed", error=str(exc))
        await repo.update_row(batch_id, result)


async def _finalize(batch_id: str, client: HospitalDirectoryClient, repo: BatchRepository) -> None:
    job = await repo.get(batch_id)
    if job is None:
        return

    all_succeeded = job.processed_hospitals == job.total_hospitals and job.failed_hospitals == 0

    if all_succeeded:
        try:
            await client.activate_batch(batch_id)
        except Exception:
            logger.exception("batch=%s activation failed after all hospitals created", batch_id)
            for row in job.sorted_rows():
                update = {"status": "activation_failed", "error": "batch activation call failed"}
                await repo.update_row(batch_id, row.model_copy(update=update))
            cleanup_status = await _attempt_cleanup(batch_id, client, job)
            await repo.finalize(
                batch_id, status="failed", batch_activated=False, cleanup_status=cleanup_status
            )
            return

        for row in job.sorted_rows():
            await repo.update_row(batch_id, row.model_copy(update={"status": "created_and_activated"}))
        await repo.finalize(batch_id, status="completed", batch_activated=True, cleanup_status="not_needed")
        return

    created_rows = [r for r in job.sorted_rows() if r.status == "created"]
    cleanup_status: CleanupStatus = "not_needed"
    if created_rows:
        cleanup_status = await _attempt_cleanup(batch_id, client, job)
        if cleanup_status == "cleaned_up":
            for row in created_rows:
                await repo.update_row(
                    batch_id,
                    row.model_copy(
                        update={
                            "hospital_id": None,
                            "status": "failed",
                            "error": "batch was not fully created; hospital removed during cleanup",
                        }
                    ),
                )
        else:
            for row in created_rows:
                update = {
                    "status": "cleanup_failed",
                    "error": "batch cleanup failed; hospital may still exist downstream, inactive",
                }
                await repo.update_row(batch_id, row.model_copy(update=update))

    await repo.finalize(batch_id, status="failed", batch_activated=False, cleanup_status=cleanup_status)


async def _attempt_cleanup(batch_id: str, client: HospitalDirectoryClient, job: BatchJob) -> CleanupStatus:
    try:
        await client.delete_batch(batch_id)
        return "cleaned_up"
    except Exception:
        logger.exception("batch=%s cleanup (delete_batch) failed", batch_id)
        return "cleanup_failed"
