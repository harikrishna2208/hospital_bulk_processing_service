from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, status

from app.clients.hospital_directory_client import DownstreamHTTPError, HospitalDirectoryClient
from app.config import Settings, get_settings
from app.repositories.batch_repository import BatchJob, batch_repository
from app.schemas.bulk import (
    BulkBatchStatusResponse,
    BulkDeleteResponse,
    BulkUploadAccepted,
    CsvValidateResponse,
)
from app.services import bulk_processing_service
from app.utils.csv_parser import CsvValidationError, parse_and_validate_csv

router = APIRouter(prefix="/hospitals/bulk", tags=["bulk"])


def _job_to_status_response(job: BatchJob) -> BulkBatchStatusResponse:
    return BulkBatchStatusResponse(
        batch_id=job.batch_id,
        status=job.status,
        total_hospitals=job.total_hospitals,
        processed_hospitals=job.processed_hospitals,
        failed_hospitals=job.failed_hospitals,
        progress_percentage=job.progress_percentage,
        processing_time_seconds=job.processing_time_seconds,
        batch_activated=job.batch_activated,
        cleanup_status=job.cleanup_status,
        hospitals=job.sorted_rows(),
    )


def _get_hospital_client(request: Request) -> HospitalDirectoryClient:
    return request.app.state.hospital_client


async def _read_csv_upload(file: UploadFile, settings: Settings) -> bytes:
    """Reads the uploaded file, rejecting it outright if it exceeds the configured size limit.

    Guards against buffering an unbounded upload into memory before CSV parsing even runs.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A CSV file upload is required")

    raw = await file.read(settings.max_upload_size_bytes + 1)
    if len(raw) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"CSV upload exceeds the maximum allowed size of {settings.max_upload_size_bytes} bytes",
        )
    return raw


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=BulkUploadAccepted)
async def upload_bulk_csv(request: Request, background_tasks: BackgroundTasks, file: UploadFile):
    settings = get_settings()
    raw = await _read_csv_upload(file, settings)

    try:
        rows = parse_and_validate_csv(raw, max_rows=settings.max_csv_rows)
    except CsvValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=[e.model_dump() for e in exc.errors],
        ) from exc

    job = await bulk_processing_service.create_batch_job(batch_repository, rows)

    client = _get_hospital_client(request)
    background_tasks.add_task(
        bulk_processing_service.run_batch, job.batch_id, rows, client, batch_repository, settings
    )

    return BulkUploadAccepted(batch_id=job.batch_id, status=job.status, total_hospitals=job.total_hospitals)


@router.post("/validate", response_model=CsvValidateResponse)
async def validate_bulk_csv(file: UploadFile):
    settings = get_settings()
    raw = await _read_csv_upload(file, settings)

    try:
        rows = parse_and_validate_csv(raw, max_rows=settings.max_csv_rows)
    except CsvValidationError as exc:
        return CsvValidateResponse(valid=False, total_rows=0, errors=exc.errors)

    return CsvValidateResponse(valid=True, total_rows=len(rows), errors=[])


@router.get("/{batch_id}", response_model=BulkBatchStatusResponse)
async def get_batch_status(batch_id: str):
    job = await batch_repository.get(batch_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return _job_to_status_response(job)


@router.post("/{batch_id}/resume", response_model=BulkBatchStatusResponse)
async def resume_batch(request: Request, batch_id: str, background_tasks: BackgroundTasks):
    job = await batch_repository.get(batch_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    failed_rows = [row for row in job.sorted_rows() if row.status == "failed"]
    if not failed_rows:
        return _job_to_status_response(job)

    rows_to_retry = [job.inputs[row.row] for row in failed_rows]
    for row in failed_rows:
        reset_row = row.model_copy(update={"status": "processing", "error": None})
        await batch_repository.update_row(batch_id, reset_row)
    await batch_repository.mark_processing(batch_id)

    client = _get_hospital_client(request)
    settings = get_settings()
    background_tasks.add_task(
        bulk_processing_service.run_batch, batch_id, rows_to_retry, client, batch_repository, settings
    )

    job = await batch_repository.get(batch_id)
    return _job_to_status_response(job)


@router.delete("/{batch_id}", response_model=BulkDeleteResponse)
async def delete_batch(request: Request, batch_id: str):
    """Deletes every hospital created under this batch_id downstream, and clears local job state.

    Intended for cleaning up test data; this does not require a local job record to exist,
    so it also works for batches created by an earlier server run.
    """
    client = _get_hospital_client(request)
    try:
        result = await client.delete_batch(batch_id)
    except DownstreamHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    await batch_repository.delete(batch_id)

    return BulkDeleteResponse(
        batch_id=batch_id,
        deleted_count=result.get("deleted_count", 0),
        message=result.get("message", "deleted"),
    )
