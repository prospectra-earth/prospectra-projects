"""
Parameterized SQL builders — one per endpoint, straight from
plans/app_implementation_plan.md §3. `parcel_obs`-backed queries are marked BLOCKED:
the table does not exist yet (see plan §0.1). They are wired end-to-end anyway per the
CEO's explicit instruction to build against the documented contract — a
TABLE_OR_VIEW_NOT_FOUND error surfaces cleanly through repository.py until NB04 lands.
"""

SEASONS = """
SELECT season, 'h3' AS grain, COUNT(DISTINCT obs_date) AS n_dates FROM silver.hex_obs GROUP BY season
UNION ALL
SELECT season, 'parcel' AS grain, COUNT(DISTINCT obs_date) AS n_dates FROM silver.parcel_obs GROUP BY season
ORDER BY season
"""

MUNICIPIOS = """
SELECT m.codigo_municipio, m.municipio, COUNT(DISTINCT p.recinto_id) AS n_parcels
FROM ref_municipios m JOIN ref_vineyard_parcels p ON p.codigo_municipio = m.codigo_municipio
GROUP BY m.codigo_municipio, m.municipio ORDER BY m.municipio
"""

DATES_H3 = """
SELECT DISTINCT obs_date, doy, item_id, tile FROM silver.hex_obs
WHERE season = :season ORDER BY obs_date
"""

DATES_PARCEL = """
SELECT DISTINCT obs_date, doy, item_id, tile FROM silver.parcel_obs
WHERE season = :season ORDER BY obs_date
"""

STATUS_HEX = """
SELECT MAX(processed_at) AS latest_hex_processed_at, COUNT(*) AS hex_obs_row_count FROM silver.hex_obs
"""

STATUS_PARCEL_COUNT = """
SELECT COUNT(*) AS parcel_obs_row_count FROM silver.parcel_obs
"""

DO_BOUNDARY = """
SELECT ST_AsGeoJSON(geometry) AS boundary_geojson FROM ref_do_boundary
"""


def hex_cells_geometry(municipio_in_clause: str) -> str:
    # ref_parcel_h3_xwalk has no coverage_fraction column live (plan §0.7 errata) — a
    # boundary-adjacent h3 cell can belong to parcels in >1 municipio, so pick one
    # deterministically (lowest recinto_id) rather than by coverage weight.
    return f"""
WITH target AS (
  SELECT x.h3_cell_id, p.codigo_municipio,
         ROW_NUMBER() OVER (PARTITION BY x.h3_cell_id ORDER BY x.recinto_id) AS rn
  FROM ref_parcel_h3_xwalk x JOIN ref_vineyard_parcels p ON p.recinto_id = x.recinto_id
  WHERE p.codigo_municipio IN ({municipio_in_clause})
)
SELECT DISTINCT CAST(t.h3_cell_id AS STRING) AS h3_cell_id, t.codigo_municipio, m.municipio,
       h3_boundaryasgeojson(t.h3_cell_id) AS boundary_geojson
FROM target t
JOIN ref_municipios m ON m.codigo_municipio = t.codigo_municipio
WHERE t.rn = 1
"""


def hex_cells_values(municipio_in_clause: str, obs_date_clause: str) -> str:
    return f"""
WITH target AS (
  SELECT x.h3_cell_id,
         ROW_NUMBER() OVER (PARTITION BY x.h3_cell_id ORDER BY x.recinto_id) AS rn
  FROM ref_parcel_h3_xwalk x JOIN ref_vineyard_parcels p ON p.recinto_id = x.recinto_id
  WHERE p.codigo_municipio IN ({municipio_in_clause})
)
SELECT CAST(h.h3_cell_id AS STRING) AS h3_cell_id, h.obs_date, h.doy,
       h.ndvi_mean, h.ndre_mean, h.ndmi_mean, h.n_px_ndvi, h.n_px_ndre, h.n_px_ndmi
FROM silver.hex_obs h JOIN target t ON t.h3_cell_id = h.h3_cell_id AND t.rn = 1
WHERE h.season = :season {obs_date_clause}
ORDER BY h.obs_date
"""


HEX_CELL_TIMESERIES = """
SELECT obs_date, doy, ndvi_mean, ndre_mean, ndmi_mean, n_px_ndvi, n_px_ndre, n_px_ndmi
FROM silver.hex_obs WHERE h3_cell_id = :h3_cell_id AND season = :season ORDER BY obs_date
"""


def parcels_geometry(municipio_in_clause: str) -> str:
    return f"""
SELECT p.recinto_id, p.codigo_municipio, m.municipio, p.superficie_m2 / 10000.0 AS area_ha,
       p.reliability_class, ST_AsGeoJSON(p.geometry) AS boundary_geojson
FROM ref_vineyard_parcels p JOIN ref_municipios m ON m.codigo_municipio = p.codigo_municipio
WHERE p.codigo_municipio IN ({municipio_in_clause})
"""


def parcels_values(municipio_in_clause: str, obs_date_clause: str) -> str:
    return f"""
SELECT recinto_id, obs_date, doy, ndvi_mean, ndvi_p50, ndvi_std,
       ndre_mean, ndre_p50, ndre_std, ndmi_mean, ndmi_p50,
       pct_valid, n_px_valid, n_px_total
FROM silver.parcel_obs
WHERE season = :season
  AND recinto_id IN (SELECT recinto_id FROM ref_vineyard_parcels WHERE codigo_municipio IN ({municipio_in_clause}))
  {obs_date_clause}
ORDER BY obs_date
"""


PARCEL_TIMESERIES = """
SELECT obs_date, doy, ndvi_mean, ndvi_p50, ndvi_std, ndre_mean, ndre_p50, ndre_std,
       ndmi_mean, ndmi_p50, pct_valid, reliability_class
FROM silver.parcel_obs WHERE recinto_id = :recinto_id AND season = :season ORDER BY obs_date
"""
