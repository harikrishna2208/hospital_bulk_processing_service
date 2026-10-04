from typing import Literal

from pydantic import BaseModel

from app.schemas.hospital import HospitalRowResult

BatchStatus = Literal["processing", "completed", "failed"]


class BulkUploadAccepted(BaseModel):
    batch_id: str
    status: BatchStatus
    total_hospitals: int


class BulkBatchStatusResponse(BaseModel):
    batch_id: str
    status: BatchStatus
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    progress_percentage: int
    processing_time_seconds: float | None = None
    batch_activated: bool
    cleanup_status: Literal["not_needed", "cleaned_up", "cleanup_failed"] | None = None
    hospitals: list[HospitalRowResult]


class CsvRowError(BaseModel):
    row: int | None = None
    error: str


class CsvValidateResponse(BaseModel):
    valid: bool
    total_rows: int
    errors: list[CsvRowError]


class BulkDeleteResponse(BaseModel):
    batch_id: str
    deleted_count: int
    message: str
