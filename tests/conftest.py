import httpx
import pytest
import respx

from app.clients.hospital_directory_client import HospitalDirectoryClient
from app.config import Settings
from app.repositories.batch_repository import BatchRepository

BASE_URL = "https://hospital-directory.test"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        hospital_directory_base_url=BASE_URL,
        request_timeout_seconds=1.0,
        max_retries=3,
        retry_base_delay_seconds=0.01,
        max_concurrency=5,
        max_csv_rows=20,
    )


@pytest.fixture
def respx_mock():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def hospital_client(settings) -> HospitalDirectoryClient:
    async with httpx.AsyncClient(base_url=BASE_URL) as http_client:
        yield HospitalDirectoryClient(http_client, settings)


@pytest.fixture
def repo() -> BatchRepository:
    return BatchRepository()


def make_csv(rows: list[tuple[str, str, str]], header: str = "name,address,phone") -> bytes:
    lines = [header]
    for name, address, phone in rows:
        lines.append(f"{name},{address},{phone}")
    return ("\n".join(lines) + "\n").encode("utf-8")
