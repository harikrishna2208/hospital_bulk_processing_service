from fastapi import APIRouter, Request

from app.clients.hospital_directory_client import HospitalDirectoryClient

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    return {"status": "healthy"}


@router.get("/health/dependencies")
async def dependencies_health_check(request: Request):
    client: HospitalDirectoryClient = request.app.state.hospital_client
    downstream_ok = await client.ping()
    return {
        "status": "healthy",
        "hospital_directory_api": "healthy" if downstream_ok else "unreachable",
    }
