from typing import Annotated

from fastapi import APIRouter, Depends, Query

from .. import models
from ..data_access import repository
from ..params import municipio_codes_param

router = APIRouter(prefix="/api/parcels", tags=["parcels"])


@router.get("/geometry", response_model=models.ListResponse[models.ParcelGeometryOut])
def get_geometry(municipio_codes: Annotated[list[str], Depends(municipio_codes_param)] = None):
    return models.envelope(repository.get_parcels_geometry(municipio_codes))


@router.get("/values", response_model=models.ListResponse[models.ParcelValueOut])
def get_values(
    season: int = Query(...),
    municipio_codes: Annotated[list[str], Depends(municipio_codes_param)] = None,
    obs_date: str | None = Query(None),
):
    return models.envelope(repository.get_parcels_values(season, municipio_codes, obs_date))


@router.get("/{recinto_id}/timeseries", response_model=models.ListResponse[models.ParcelTimeseriesPointOut])
def get_timeseries(recinto_id: str, season: int = Query(...)):
    return models.envelope(repository.get_parcel_timeseries(recinto_id, season))
