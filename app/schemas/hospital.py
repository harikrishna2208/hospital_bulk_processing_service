from typing import Literal

from pydantic import BaseModel

RowStatus = Literal[
    "processing",
    "created",
    "created_and_activated",
    "failed",
    "cleanup_failed",
    "activation_failed",
]


class HospitalRowResult(BaseModel):
    row: int
    hospital_id: int | None = None
    name: str
    status: RowStatus
    error: str | None = None


class HospitalInput(BaseModel):
    row: int
    name: str
    address: str
    phone: str | None = None
