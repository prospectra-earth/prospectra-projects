from fastapi import APIRouter, Query

from .. import cache, models
from ..data_access import repository

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta/seasons", response_model=models.ListResponse[models.SeasonOut])
@cache.cached_seasons
def list_seasons():
    return models.envelope(repository.get_seasons())


@router.get("/meta/municipios", response_model=models.ListResponse[models.MunicipioOut])
@cache.cached_municipios
def list_municipios():
    return models.envelope(repository.get_municipios())


@router.get("/meta/dates", response_model=models.ListResponse[models.DateOut])
@cache.cached_dates
def list_dates(season: int = Query(...), grain: str = Query(..., pattern="^(h3|parcel)$")):
    return models.envelope(repository.get_dates(season, grain))


@router.get("/meta/status", response_model=models.StatusOut)
@cache.cached_status
def get_status():
    return repository.get_status()


@router.get("/do-boundary", response_model=models.BoundaryOut)
@cache.cached_boundary
def get_do_boundary():
    return repository.get_do_boundary()
