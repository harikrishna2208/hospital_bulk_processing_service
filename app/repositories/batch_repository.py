import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

from app.schemas.hospital import HospitalInput, HospitalRowResult

BatchStatus = Literal["processing", "completed", "failed"]
CleanupStatus = Literal["not_needed", "cleaned_up", "cleanup_failed"]


@dataclass
class BatchJob:
    batch_id: str
    total_hospitals: int
    status: BatchStatus = "processing"
    batch_activated: bool = False
    cleanup_status: CleanupStatus | None = None
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    rows: dict[int, HospitalRowResult] = field(default_factory=dict)
    inputs: dict[int, HospitalInput] = field(default_factory=dict)

    @property
    def processed_hospitals(self) -> int:
        return sum(1 for r in self.rows.values() if r.status != "processing")

    @property
    def failed_hospitals(self) -> int:
        failed_statuses = ("failed", "activation_failed", "cleanup_failed")
        return sum(1 for r in self.rows.values() if r.status in failed_statuses)

    @property
    def processing_time_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return round(self.ended_at - self.started_at, 3)

    @property
    def progress_percentage(self) -> int:
        if self.total_hospitals == 0:
            return 100
        return int((self.processed_hospitals / self.total_hospitals) * 100)

    def sorted_rows(self) -> list[HospitalRowResult]:
        return [self.rows[row] for row in sorted(self.rows)]


class BatchRepository:
    """In-memory, asyncio-safe store of batch jobs. Replace with Redis/DB if persistence is needed."""

    def __init__(self):
        self._jobs: dict[str, BatchJob] = {}
        self._lock = asyncio.Lock()

    async def create(self, batch_id: str, inputs: list[HospitalInput]) -> BatchJob:
        job = BatchJob(batch_id=batch_id, total_hospitals=len(inputs))
        job.inputs = {i.row: i for i in inputs}
        job.rows = {
            i.row: HospitalRowResult(row=i.row, name=i.name, status="processing") for i in inputs
        }
        async with self._lock:
            self._jobs[batch_id] = job
        return job

    async def get(self, batch_id: str) -> BatchJob | None:
        async with self._lock:
            return self._jobs.get(batch_id)

    async def update_row(self, batch_id: str, row_result: HospitalRowResult) -> None:
        async with self._lock:
            job = self._jobs.get(batch_id)
            if job is not None:
                job.rows[row_result.row] = row_result

    async def finalize(
        self,
        batch_id: str,
        *,
        status: BatchStatus,
        batch_activated: bool,
        cleanup_status: CleanupStatus | None,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(batch_id)
            if job is not None:
                job.status = status
                job.batch_activated = batch_activated
                job.cleanup_status = cleanup_status
                job.ended_at = time.monotonic()

    async def mark_processing(self, batch_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(batch_id)
            if job is not None:
                job.status = "processing"
                job.ended_at = None

    async def delete(self, batch_id: str) -> None:
        async with self._lock:
            self._jobs.pop(batch_id, None)


batch_repository = BatchRepository()
