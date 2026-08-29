import json

from .. import db
from . import queries


class TableNotReadyError(RuntimeError):
    """Raised when a query hits a table that the pipeline hasn't populated/created yet
    (parcel_obs today — see plans/app_implementation_plan.md §0.1)."""


def _normalize_codes(codes: list[str]) -> list[str]:
    # sort/uppercase/dedupe so equivalent filter selections produce identical SQL text
    # and hit Databricks' deterministic-query result cache (plan §3).
    return sorted({c.strip().upper() for c in codes if c.strip()})


def _in_clause(codes: list[str], prefix: str) -> tuple[str, dict]:
    normalized = _normalize_codes(codes)
    if not normalized:
        raise ValueError("municipio_codes must be non-empty")
    placeholders = [f":{prefix}{i}" for i in range(len(normalized))]
    params = {f"{prefix}{i}": code for i, code in enumerate(normalized)}
    return ", ".join(placeholders), params


def _run_parcel_obs_query(query: str, params: dict) -> list[dict]:
    try:
        return db.run_query(query, params)
    except Exception as exc:  # databricks-sql-connector raises its own ServerOperationError
        if "TABLE_OR_VIEW_NOT_FOUND" in str(exc) and "parcel_obs" in str(exc):
            raise TableNotReadyError(
                "parcel_obs does not exist yet — the pipeline (04_parcel_indices.py) hasn't "
                "written it. See plans/app_implementation_plan.md §0.1."
            ) from exc
        raise


def _parse_geojson_rows(rows: list[dict], field: str = "boundary_geojson") -> list[dict]:
    for row in rows:
        if row.get(field) is not None:
            row[field] = json.loads(row[field])
    return rows


# ---- meta ----

def get_seasons() -> list[dict]:
    try:
        rows = db.run_query(queries.SEASONS)
    except Exception as exc:
        if "TABLE_OR_VIEW_NOT_FOUND" in str(exc) and "parcel_obs" in str(exc):
            # parcel_obs doesn't exist yet — fall back to hex_obs only (plan §0.1)
            rows = db.run_query(
                "SELECT season, 'h3' AS grain, COUNT(DISTINCT obs_date) AS n_dates "
                "FROM hex_obs GROUP BY season ORDER BY season"
            )
        else:
            raise
    by_season: dict[int, dict] = {}
    for row in rows:
        entry = by_season.setdefault(row["season"], {"season": row["season"], "grains_available": [], "n_dates": 0})
        entry["grains_available"].append(row["grain"])
        entry["n_dates"] = max(entry["n_dates"], row["n_dates"])
    return sorted(by_season.values(), key=lambda e: e["season"])


def get_municipios() -> list[dict]:
    return db.run_query(queries.MUNICIPIOS)


def get_dates(season: int, grain: str) -> list[dict]:
    query = queries.DATES_H3 if grain == "h3" else queries.DATES_PARCEL
    if grain == "parcel":
        return _run_parcel_obs_query(query, {"season": season})
    return db.run_query(query, {"season": season})


def get_status() -> dict:
    hex_row = db.run_query_one(queries.STATUS_HEX) or {}
    parcel_obs_available = True
    parcel_row_count = None
    try:
        parcel_row = db.run_query_one(queries.STATUS_PARCEL_COUNT)
        parcel_row_count = parcel_row["parcel_obs_row_count"] if parcel_row else 0
    except Exception as exc:
        if "TABLE_OR_VIEW_NOT_FOUND" in str(exc) and "parcel_obs" in str(exc):
            parcel_obs_available = False
        else:
            raise
    return {
        "warehouse_connected": True,
        "latest_hex_processed_at": hex_row.get("latest_hex_processed_at"),
        "hex_obs_row_count": hex_row.get("hex_obs_row_count", 0),
        "parcel_obs_available": parcel_obs_available,
        "parcel_obs_row_count": parcel_row_count,
    }


def get_do_boundary() -> dict:
    row = db.run_query_one(queries.DO_BOUNDARY)
    return {"boundary_geojson": json.loads(row["boundary_geojson"])} if row else {"boundary_geojson": None}


# ---- H3 grain ----

def get_hex_cells_geometry(season: int, municipio_codes: list[str]) -> list[dict]:
    clause, params = _in_clause(municipio_codes, "m")
    rows = db.run_query(queries.hex_cells_geometry(clause), params)
    return _parse_geojson_rows(rows)


def get_hex_cells_values(season: int, municipio_codes: list[str], obs_date: str | None) -> list[dict]:
    clause, params = _in_clause(municipio_codes, "m")
    params["season"] = season
    obs_date_clause = ""
    if obs_date:
        obs_date_clause = "AND h.obs_date = :obs_date"
        params["obs_date"] = obs_date
    return db.run_query(queries.hex_cells_values(clause, obs_date_clause), params)


def get_hex_cell_timeseries(h3_cell_id: str, season: int) -> list[dict]:
    return db.run_query(queries.HEX_CELL_TIMESERIES, {"h3_cell_id": int(h3_cell_id), "season": season})


# ---- parcel grain ----

def get_parcels_geometry(municipio_codes: list[str]) -> list[dict]:
    clause, params = _in_clause(municipio_codes, "m")
    rows = db.run_query(queries.parcels_geometry(clause), params)
    return _parse_geojson_rows(rows)


def get_parcels_values(season: int, municipio_codes: list[str], obs_date: str | None) -> list[dict]:
    clause, params = _in_clause(municipio_codes, "m")
    params["season"] = season
    obs_date_clause = ""
    if obs_date:
        obs_date_clause = "AND obs_date = :obs_date"
        params["obs_date"] = obs_date
    return _run_parcel_obs_query(queries.parcels_values(clause, obs_date_clause), params)


def get_parcel_timeseries(recinto_id: str, season: int) -> list[dict]:
    return _run_parcel_obs_query(queries.PARCEL_TIMESERIES, {"recinto_id": recinto_id, "season": season})
