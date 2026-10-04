import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.routes import bulk_hospitals, health
from app.clients.hospital_directory_client import HospitalDirectoryClient
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(base_url=settings.hospital_directory_base_url, limits=limits) as http_client:
        app.state.hospital_client = HospitalDirectoryClient(http_client, settings)
        yield


app = FastAPI(
    title="Hospital Bulk Processing Service",
    description=(
        "Orchestration service that bulk-creates hospitals, via CSV upload, through the "
        "downstream Hospital Directory API (https://hospital-directory.onrender.com)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(bulk_hospitals.router)
