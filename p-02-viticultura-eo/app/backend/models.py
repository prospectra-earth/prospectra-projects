from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Meta(BaseModel):
    row_count: int


class ListResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: Meta


def envelope(items: list[T]) -> dict:
    return {"data": items, "meta": {"row_count": len(items)}}


# ---- meta ----

class SeasonOut(BaseModel):
    season: int
    grains_available: list[str]
    n_dates: int


class MunicipioOut(BaseModel):
    codigo_municipio: str
    municipio: str
    n_parcels: int


class DateOut(BaseModel):
    obs_date: date
    doy: int
    item_id: str
    tile: str


class StatusOut(BaseModel):
    warehouse_connected: bool
    latest_hex_processed_at: datetime | None
    hex_obs_row_count: int
    parcel_obs_available: bool
    parcel_obs_row_count: int | None = None


class BoundaryOut(BaseModel):
    boundary_geojson: dict


# ---- H3 grain ----

class HexCellGeometryOut(BaseModel):
    h3_cell_id: str
    codigo_municipio: str
    municipio: str
    boundary_geojson: dict


class HexCellValueOut(BaseModel):
    h3_cell_id: str
    obs_date: date
    doy: int
    ndvi_mean: float | None
    ndre_mean: float | None
    ndmi_mean: float | None
    n_px_ndvi: int | None
    n_px_ndre: int | None
    n_px_ndmi: int | None


class HexCellTimeseriesPointOut(BaseModel):
    obs_date: date
    doy: int
    ndvi_mean: float | None
    ndre_mean: float | None
    ndmi_mean: float | None
    n_px_ndvi: int | None
    n_px_ndre: int | None
    n_px_ndmi: int | None


# ---- parcel grain ----

class ParcelGeometryOut(BaseModel):
    recinto_id: str
    codigo_municipio: str
    municipio: str
    area_ha: float
    reliability_class: str
    boundary_geojson: dict


class ParcelValueOut(BaseModel):
    recinto_id: str
    obs_date: date
    doy: int
    ndvi_mean: float | None
    ndvi_p50: float | None
    ndvi_std: float | None
    ndre_mean: float | None
    ndre_p50: float | None
    ndre_std: float | None
    ndmi_mean: float | None
    ndmi_p50: float | None
    pct_valid: float | None
    n_px_valid: int | None
    n_px_total: int | None


class ParcelTimeseriesPointOut(BaseModel):
    obs_date: date
    doy: int
    ndvi_mean: float | None
    ndvi_p50: float | None
    ndvi_std: float | None
    ndre_mean: float | None
    ndre_p50: float | None
    ndre_std: float | None
    ndmi_mean: float | None
    ndmi_p50: float | None
    pct_valid: float | None
    reliability_class: str
