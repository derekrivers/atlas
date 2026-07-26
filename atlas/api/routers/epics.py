"""Epic collection routes."""

from fastapi import APIRouter

from atlas.api.dependencies import EpicsDependency
from atlas.api.schemas import EpicsResponse

router = APIRouter(prefix="/epics", tags=["epics"])


@router.get("", response_model=EpicsResponse)
def list_epics(epics: EpicsDependency) -> EpicsResponse:
    return epics
