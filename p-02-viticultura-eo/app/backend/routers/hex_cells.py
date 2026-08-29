from typing import Annotated

from fastapi import APIRouter, Depends, Query

from .. import models
from ..data_access import repository
from ..params import municipio_codes_param

router = APIRouter(prefix="/api/hex-cells", tags=["hex-cells"])


@router.get("/geometry", response_model=models.ListResponse[models.HexCellGeometryOut])
def get_geometry(
    season: int = Query(...),
    municipio_codes: Annotated[list[str], Depends(municipio_codes_param)] = None,
):
    return models.envelope(repository.get_hex_cells_geometry(season, municipio_codes))


@router.get("/values", response_model=models.ListResponse[models.HexCellValueOut])
def get_values(
    season: int = Query(...),
    municipio_codes: Annotated[list[str], Depends(municipio_codes_param)] = None,
    obs_date: str | None = Query(None),
):
    return models.envelope(repository.get_hex_cells_values(season, municipio_codes, obs_date))


@router.get("/{h3_cell_id}/timeseries", response_model=models.ListResponse[models.HexCellTimeseriesPointOut])
def get_timeseries(h3_cell_id: str, season: int = Query(...)):
    return models.envelope(repository.get_hex_cell_timeseries(h3_cell_id, season))
