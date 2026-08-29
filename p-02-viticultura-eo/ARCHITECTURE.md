# `viticultura-eo` — Pipeline Architecture

Sentinel-2 satellite vineyard intelligence for **DO Ribera del Duero**: five notebooks turn a
public satellite archive and a public cadastral dataset into per-parcel and per-hex vegetation
time series, ready for a yield-forecast backtest and an interactive map. Built for a named buyer
(the Consejo Regulador), not as a portfolio piece — see `decisions/D-04_commercial_pivot_viticultura_eo.md`.

All five notebooks are built and run on Databricks Free Edition (serverless). `03_parcel_indices`
revives H3 — a decision `plans/PLAN_04_parcel_indices.md` §7 explicitly deferred — because GeoBrix's
H3 rasterization call turned out to be one extra line once the rest of the pipeline existed, not a
rebuild. The plan doc is otherwise a reliable design record except where noted below.

## Data flow

```
MAPA boundary zip (manual)  ─┐
SIGPAC zips (manual)        ─┼─► 00_setup (constants + helpers, writes nothing)
municipios_geo_raw (P-01)   ─┘         │
                                        ▼
                              region_geometries/reference_geometry_ribera  (run once per DO)
                              ├─ ref_do_boundary
                              ├─ ref_municipios
                              ├─ ref_search_envelope ───────┐
                              ├─ ref_vineyard_parcels ───┐   │
                              └─ ref_uso_audit           │   │
                                                          │   ▼
                                            Earth Search  │  01_ingest_imagery  (recurring, one season/run)
                                            STAC API ─────┼─►  └─ stac_items
                                                          │        │
                                                          │        ▼
                                                          └─►02_download_assets  (recurring, idempotent)
                                                             (windowed COG download,           │
                                                              per-tile bbox from parcels)       ▼
                                                             └─ s2_assets              03_parcel_indices
                                                                                        ├─ ref_parcel_h3_xwalk (static)
                                                                                        ├─ parcel_obs
                                                                                        └─ hex_obs
```

## Notebooks

| # | Notebook | Cadence | Reads | Writes |
|---|---|---|---|---|
| — | `00_setup` | every run (`%run`) | — | nothing — constants, table-name globals, STAC/SIGPAC helpers into the caller's namespace |
| — | `region_geometries/reference_geometry_ribera` | once per DO (cadastre is near-static) | MAPA boundary zip, SIGPAC `RECFE` zips (both manual downloads), `municipios_geo_raw` (P-01 table) | `ref_do_boundary`, `ref_municipios`, `ref_search_envelope`, `ref_vineyard_parcels`, `ref_uso_audit` |
| 01 | `01_ingest_imagery` | recurring, one `season` (calendar year) per run | `ref_search_envelope`, Earth Search STAC API | `stac_items` |
| 02 | `02_download_assets` | recurring, idempotent | `stac_items`, `ref_vineyard_parcels` | `s2_assets` |
| 03 | `03_parcel_indices` | recurring, one `season` per run | `s2_assets`, `ref_vineyard_parcels`, `stac_items` (footprints only) | `ref_parcel_h3_xwalk` (built once, refreshed on parcel change), `parcel_obs`, `hex_obs` |

**What each one actually does:**

- **01** — two-phase, manual download in between. Phase 1 gets the DO boundary and the
  municipality↔SIGPAC crosswalk (matched by normalized name — INE and cadastral codes collide) and
  writes the STAC search bbox. Phase 2 (after `scripts/fetch_sigpac.sh` runs) filters SIGPAC to
  `uso='VI'`, clips to the DO, repairs invalid geometries, and computes **pure-pixel buffered
  geometries** per parcel: `geom_b05` (−5 m, for 10 m bands/NDVI) and `geom_b10` (−10 m, for 20 m
  bands/NDRE, NDMI), plus `reliability_class` (`'parcel'` if the buffer survives, else
  `'aggregate_only'` — ~60% of parcels, ~10% of area, flagged not dropped).
- **02** — metadata-only STAC search, no pixels read. Full calendar year per run (not a growing-season
  window), `replaceWhere`-writes just that season's slice so re-running one year never wipes others.
  Loose cloud-cover filter is intentional — real filtering happens per-pixel via SCL in NB04.
- **03** — downloads windowed COGs (range reads over `/vsicurl`, not whole tiles) into a Volume, one
  bbox per tile sized to that tile's vineyard extent. Tile list auto-derives from `stac_items` if left
  blank. Idempotent: a `NOT EXISTS` dedup check against `is_out_file_valid` skips anything already
  staged, so invalid rows are simply retried on the next run — there is no separate repair step.
- **04** — per `item_id`: decode (`DN*scale+offset`), resample 20 m bands to 10 m (nearest), compute
  NDVI/NDRE/NDMI via GeoBrix `rst_index`-equivalent math with a guarded denominator (measured NDVI can
  blow past 1 when atmospheric correction drives red near zero — SCL alone doesn't catch it) and an
  SCL mask (keep classes 4/5, vegetation+bare). Writes at two grains from the same decoded raster:
  `parcel_obs` (`rst_clip` to each parcel's buffer + `rst_apply` to a stats struct — *not*
  `rst_rasterize_agg`, which the plan assumed did zonal stats but actually only burns one raster) and
  `hex_obs` (H3 res-12, via `gbx_rst_h3_rastertogridavg`/`...count`, reprojected to 4326 first).

## Tables produced

All in `geospatial.ribera_duero` unless noted.

| Table | Grain | Contents | From |
|---|---|---|---|
| `spain_population_analysis.municipios_geo_raw` | 1 row / municipality | Spanish municipal polygons (external, read-only) | P-01 (sibling project) |
| `ref_do_boundary` | 1 row | DO legal boundary polygon (MAPA, 2014 vintage — covers 82 of 102 municipalities) | 01 |
| `ref_municipios` | ~82 rows | Municipality list + INE↔cadastral crosswalk + ITACyL download URLs + `frac_inside` | 01 |
| `ref_search_envelope` | 1 row | STAC search bbox (boundary + 2 km) — single source of truth for it | 01 |
| `ref_vineyard_parcels` | ~69,785 rows, 1/SIGPAC recinto (`uso='VI'`) | **Core deliverable.** Geometry, area, plus `geom_b05`/`geom_b10` pure-pixel buffers, `has_pure_pixel_10m/20m`, `reliability_class` | 01 |
| `ref_uso_audit` | 1 row / land-use code | Area by land-use code — evidence trail for the VI-only filter | 01 |
| `stac_items` | 1 row / (item, band) | Sentinel-2 scene+band metadata: COG href, `scale`/`offset`/`nodata` (needed to decode reflectance — baseline is not monotonic in time), cloud cover, footprint | 02 |
| `s2_assets` | 1 row / (item, band) | Manifest of windowed, downloaded COGs: Volume path, `is_out_file_valid`, radiometry copied forward from `stac_items` | 03 |
| `ref_parcel_h3_xwalk` | 1 row / (recinto_id, h3_cell_id) | Static parcel→hex membership, full unbuffered footprint, `h3_coverash3` (not `polyfillash3` — the latter drops 10–12% of thin parcels) | 04 |
| `parcel_obs` | 1 row / (recinto_id, obs_date) | **Consejo-facing output.** `ndvi/ndre/ndmi_mean/p50/std`, `n_px_total`, `n_px_valid`, `pct_valid`, `scl_classes`, `reliability_class` | 04 |
| `hex_obs` | 1 row / (h3_cell_id, obs_date) | Within-parcel resolution a parcel mean erases — `ndvi/ndre/ndmi_mean` + pixel counts per hex | 04 |

## External data sources

| Source | Enters at | Notes |
|---|---|---|
| MAPA "Calidad Diferenciada — Vinos" appellation layer | NB01, manual download | DO legal boundary only, no parcels; stale (2014) |
| SIGPAC `RECFE` shapefiles (ITACyL FTP) | NB01, manual download via `scripts/fetch_sigpac.sh` | Parcel geometry + land use; **licence forbids commercial exploitation** — unresolved, see `README.md` |
| Earth Search STAC API (`sentinel-2-l2a`) | NB02 (search) / NB03 (pixels) | Public, unsigned, no auth |
| Consejo Regulador published stats (inscribed ha, yield caps) | hardcoded in the relevant notebook | Manually transcribed, not fetched programmatically |

## Outcome

End to end, the pipeline turns two public sources (Sentinel-2 imagery + SIGPAC cadastre) into two
analysis-ready tables: **`parcel_obs`**, one row per vineyard parcel per satellite pass with
NDVI/NDRE/NDMI and a data-quality flag, and **`hex_obs`**, the same signal at finer within-parcel
resolution via H3. Both carry `recinto_id`/`h3_cell_id` provenance back to `ref_vineyard_parcels`,
so results can be rolled up to municipality or DO level and checked against the Consejo's published
inscribed-area and yield figures. Not yet built: the yield-backtest harness and the map/app front end
referenced in `README.md`.
