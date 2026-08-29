# `viticultura-eo` App — FastAPI + React + Plotly Spec (derisked)

## 0. Derisking results (executed against live warehouse, 2026-08-22)

The queries below were actually run against `geospatial.ribera_duero` on the live SQL
Warehouse (not estimated). Three findings materially change the spec from the first draft.

### 0.1 BLOCKER — `parcel_obs` does not exist; `hex_obs` exists but is empty

`SHOW TABLES` in `geospatial.ribera_duero` returns 9 tables. `parcel_obs` is **not** one of
them. `hex_obs` exists (correct schema) but `SELECT COUNT(*) FROM hex_obs` = **0**.

```
| database     | tableName            |
| ribera_duero | hex_obs               |   <- exists, 0 rows
| ribera_duero | ref_do_boundary       |
| ribera_duero | ref_municipios        |
| ribera_duero | ref_parcel_h3_xwalk   |
| ribera_duero | ref_search_envelope   |
| ribera_duero | ref_uso_audit         |
| ribera_duero | ref_vineyard_parcels  |
| ribera_duero | s2_assets             |   <- 60 rows only (season 2025, 4 tiles — a partial test run)
| ribera_duero | stac_items            |   <- 11,304 rows (full metadata catalog, seasons 2022-2025)
```

`04_parcel_indices.py` (NB04) has only partially executed: it built
`ref_parcel_h3_xwalk` (1,661,467 rows, real) but never reached the `parcel_obs` CTAS, and its
`hex_obs` write produced zero rows. `s2_assets` (NB03's downloaded-COG manifest) has only 60
rows total — a small single-season test slice, not a full multi-season ingest — so even NB04
re-running today would only be able to compute indices for that same thin slice, not the
2022–2025 range `stac_items` already has metadata for.

**Practical consequence: there is currently no NDVI/NDRE/NDMI observation data anywhere in
this schema, at either grain.** The reference/geometry tables (`ref_vineyard_parcels`,
`ref_municipios`, `ref_do_boundary`, `ref_parcel_h3_xwalk`) are real and complete; the
value-bearing tables the map is supposed to color are not populated yet. This is the single
biggest risk in the whole spec and supersedes the performance-tuning risk the first draft
focused on — see the question at the end of this document.

### 0.2 Row counts (real, replacing schema-based estimates)

| Table | Rows | Note |
|---|---|---|
| `ref_vineyard_parcels` | 69,785 | matches `ARCHITECTURE.md` estimate exactly |
| `ref_municipios` | 82 | matches estimate |
| `ref_do_boundary` | 1 | |
| `ref_parcel_h3_xwalk` | 1,661,467 | (recinto_id, h3_cell_id) pairs |
| `hex_obs` | **0** | schema exists, no data |
| `parcel_obs` | **table does not exist** | |
| `stac_items` | 11,304 | scene+band metadata, seasons 2022–2025 |
| `s2_assets` | 60 | downloaded COGs — season 2025 only, 4 tiles — a test slice |

`reliability_class` on `ref_vineyard_parcels` (the only place it currently lives, since
`parcel_obs` doesn't exist to cross-check against — risk §8.5 from the first draft is now
**blocked, not resolved**, pending NB04 actually running):

| reliability_class | n parcels | % parcels | total ha | % area |
|---|---|---|---|---|
| `parcel` | 52,075 | 74.6% | 30,083.6 | 93.4% |
| `aggregate_only` | 17,710 | 25.4% | 637.4 | 2.0% |

Note this is meaningfully different from `ARCHITECTURE.md`'s prose estimate ("~60% of
parcels, ~10% of area" are `aggregate_only`) — worth a one-line correction to that doc
separately; not an app-spec blocker.

### 0.3 H3-boundary SQL function — CONFIRMED WORKING (reverses the first draft's decision)

The first draft treated `h3_boundaryasgeojson`/`h3_boundaryaswkb`/`h3_boundaryaswkt` as
unverified (per `04_parcel_indices.py`'s own inline comment) and defaulted to computing H3
boundaries client-side with `h3-js`. Live check:

```sql
SHOW FUNCTIONS LIKE '*h3_boundary*';
-- h3_boundaryasgeojson, h3_boundaryaswkb, h3_boundaryaswkt   (all present)

SELECT h3_cell_id, h3_boundaryasgeojson(h3_cell_id) FROM ref_parcel_h3_xwalk LIMIT 1;
-- 631509696261089791 -> {"type":"Polygon","coordinates":[[[-3.764558468,41.623479762], ...]]}
```

Valid GeoJSON `Polygon`, correct coordinate order, plausible size/location for a Ribera del
Duero H3 res-12 cell. **Decision reversed: `/api/hex-cells/geometry` now calls
`h3_boundaryasgeojson` server-side and returns `boundary_geojson` directly**, exactly
mirroring `/api/parcels/geometry`'s shape. This drops the `h3-js` npm dependency entirely,
removes a whole client-side geometry-construction code path, and unifies both grain
endpoints on the same response contract (`{id, codigo_municipio, municipio,
boundary_geojson}`). Section 5's frontend map code no longer needs an H3-specific
GeoJSON-building branch.

### 0.4 Timebar threshold — the ~50-100k-row guess was far too high

The §2 fetch-all-vs-per-tick heuristic assumed the boundary was in the tens of thousands of
rows. Real cell counts per municipio, measured directly:

```sql
-- distinct H3 res-12 cells belonging to parcels in ONE mid-sized municipio (Aranda de Duero, 09018)
SELECT COUNT(DISTINCT h3_cell_id) FROM ref_parcel_h3_xwalk x
JOIN ref_vineyard_parcels p ON p.recinto_id = x.recinto_id
WHERE p.codigo_municipio = '09018';
-- 67,480 cells
```

Aranda de Duero has 4,977 parcels — the **2nd largest** of 82 municipios by parcel count (San
Esteban de Gormaz, the largest, has 5,955 — proportionally even more cells). **One single
mid-size municipio alone already produces 67,480 H3 cells** — before multiplying by ~15–25
dates/season. That's ~1.0–1.7M rows for one municipio's full-season `/values` payload, an
order of magnitude past the original ~50-100k threshold.

**Revised strategy:** for H3 grain, treat "fetch all dates once" as the exception, not the
default — it only applies to the smallest handful of municipios (the tail below ~1,000
parcels) or a single-date view. Default behavior: always fetch `/values` per `obs_date` on
slider release / play-tick, with 1–2-date prefetch during Play; never attempt an
all-dates-at-once fetch above ~10k cells (revised down from 50-100k) without the user
explicitly narrowing the municipio selection. Parcel grain is unaffected — it's bounded by
69,785 parcels DO-wide, so even a full-DO parcel-grain `/values` for one date is one order of
magnitude smaller than one municipio's H3 cells for one date.

### 0.5 Query plan / cache housekeeping

`EXPLAIN` on the `/api/hex-cells/geometry` CTE join for `codigo_municipio = '09018'` runs
fully on Photon with broadcast joins on both sides (parcel↔xwalk and municipio) — plan shape
is healthy, no shuffle-heavy join to worry about. The optimizer does flag missing table
statistics:

```
Optimizer Statistics (table names per statistics state)
  missing = ref_municipios, ref_vineyard_parcels
  full    = ref_parcel_h3_xwalk
```

Cheap pre-deploy step, not a blocker: `ANALYZE TABLE ref_vineyard_parcels COMPUTE STATISTICS
FOR ALL COLUMNS;` and same for `ref_municipios` — improves the optimizer's broadcast-vs-shuffle
decisions once real traffic hits these joins repeatedly.

### 0.6 `ST_AsGeoJSON` on `ref_do_boundary` — confirmed working

`SELECT ST_AsGeoJSON(geometry) FROM ref_do_boundary` returns a valid GeoJSON `Polygon` for
the DO outline. `/api/do-boundary` works as speced, no change.

---

## Context

The `viticultura-eo` pipeline (`p-02-viticultura-eo`) now has a working clickable-prototype
UI mockup (`app/design/Main.dc.html`, Design Canvas format) built with illustrative sample
data in a sandboxed Artifact environment. The user wants to replace that mock with a real
Databricks App: a FastAPI backend reading live data from a SQL Warehouse, and a React
frontend using Plotly for the map, deployed as a genuine internet-facing app (not sandboxed —
real basemap tiles are fine).

The mock is the explicit source of truth for look-and-feel *and* feature scope. The tables it
must actually read are the outputs of `04_parcel_indices.py`: `hex_obs`, `parcel_obs`,
`ref_parcel_h3_xwalk`, plus the reference tables from `01_reference_geometry.py`
(`ref_vineyard_parcels`, `ref_municipios`, `ref_do_boundary`) that supply geometry and
municipality names — **`hex_obs`/`parcel_obs` are not populated yet, see §0.1**. The user
explicitly wants plain REST (FastAPI + React), **not** Databricks' own "AppKit" (Node/TS/
proto-contract) framework, which is that platform's documented default — this is a
deliberate deviation the spec holds to.

All facts below (table DDL, CRS, native `ST_*`/H3 SQL functions, Databricks Apps platform
constraints) were confirmed by reading the project's own notebooks directly, two research
passes over the installed `databricks` Claude Code plugin's skill docs, and — as of §0 above —
by executing every load-bearing query against the live warehouse.

---

## 1. Ground truth: tables the app reads

Catalog/schema: **`geospatial.ribera_duero`** (`NS` in `00_setup.py`). CRS: store =
**EPSG:4258** (ETRS89 geographic), measure = **EPSG:25830** (ETRS89/UTM 30N). `H3_RES` is a
notebook **widget default of `12`**, not a hardcoded constant — the app must not bake in
"res 12" as gospel; derive/display it from data.

```sql
-- hex_obs — H3 grain, no p50/std, no reliability_class. EXISTS, 0 rows today (see §0.1).
CREATE TABLE hex_obs (
  h3_cell_id BIGINT, obs_date DATE, season INT, doy INT, item_id STRING, tile STRING,
  ndvi_mean DOUBLE, ndre_mean DOUBLE, ndmi_mean DOUBLE,
  n_px_ndvi INT, n_px_ndre INT, n_px_ndmi INT, processed_at TIMESTAMP
) CLUSTER BY (h3_cell_id, obs_date)

-- parcel_obs — SIGPAC parcel grain, has p50/std (ndmi has no std), reliability_class.
-- DOES NOT EXIST YET as a table (see §0.1) — DDL below is from the notebook source, unverified live.
CREATE TABLE parcel_obs (
  recinto_id STRING, codigo_municipio STRING, obs_date DATE, season INT, doy INT,
  item_id STRING, tile STRING, reliability_class STRING,
  ndvi_mean DOUBLE, ndvi_p50 DOUBLE, ndvi_std DOUBLE,
  ndre_mean DOUBLE, ndre_p50 DOUBLE, ndre_std DOUBLE,
  ndmi_mean DOUBLE, ndmi_p50 DOUBLE,
  n_px_total INT, n_px_valid INT, pct_valid DOUBLE, scl_classes MAP<INT, BIGINT>,
  processed_at TIMESTAMP
) CLUSTER BY (recinto_id, obs_date)

-- ref_parcel_h3_xwalk — static parcel<->hex membership. CONFIRMED, 1,661,467 rows.
CREATE TABLE ref_parcel_h3_xwalk (
  recinto_id STRING, h3_cell_id BIGINT, coverage_fraction DOUBLE, built_at TIMESTAMP
)

-- ref_vineyard_parcels — CONFIRMED, 69,785 rows.
-- columns: sigpac_year, recinto_id, codigo_municipio, codigo_catastral, municipio, uso_sigpac,
--   superficie_m2, perimetro_m, coef_regadio, poligono, parcela, recinto,
--   geometry (GEOMETRY, 4258 — full unbuffered footprint), geometry_repaired,
--   geom_b05, geom_b10 (pure-pixel buffers at -5/-10 m), area_b05_m2, area_b10_m2,
--   min_width_b05_m, min_width_b10_m, has_pure_pixel_10m, has_pure_pixel_20m,
--   reliability_class ('parcel' | 'aggregate_only'), loaded_at
) CLUSTER BY (codigo_municipio)

-- ref_municipios — CONFIRMED, 82 rows.
-- columns: codigo_municipio, codigo_catastral, municipio, codigo_provincia, geometry,
--   muni_ha, inside_ha, frac_inside, is_partial, codes_differ, zip_name, url,
--   sigpac_year, extracted_at

-- ref_do_boundary — CONFIRMED, 1 row, `geometry` column, DO legal boundary (MAPA, 2014 vintage)
```

Native spatial SQL confirmed available and working: `ST_Buffer`, `ST_Transform`, `ST_Area`,
`ST_Perimeter`, `ST_Intersects`, `ST_IsValid`, `ST_AsGeoJSON(geo) -> STRING`,
`ST_GeomFromGeoJSON`, `h3_coverash3`, and — newly confirmed live, see §0.3 —
**`h3_boundaryasgeojson(h3_cell_id) -> STRING`**. Requires DBR 17.1+/serverless (already the
project's runtime). Spatial joins get automatic indexing/broadcast optimization on serverless
warehouses. **H3 boundary decision: server-side `h3_boundaryasgeojson`, not client-side
`h3-js`** (reversed from the first draft — see §0.3).

---

## 2. REST API

All read-only `GET`s under `/api/*`, kebab-case plural nouns, no AppKit/proto layer. List
responses: `{"data": [...], "meta": {"row_count": N}}`.

**Meta (slow-changing, cached — see §7):**

| Endpoint | Params | Returns | Backs |
|---|---|---|---|
| `GET /api/meta/seasons` | — | `[{season, grains_available, n_dates}]` | Season chips (dynamic — **today this will return an empty or near-empty list, see §0.1**) |
| `GET /api/meta/municipios` | — | `[{codigo_municipio, municipio, n_parcels}]` | Municipality checklist — real ~82 |
| `GET /api/meta/dates` | `season`, `grain` | `[{obs_date, doy, item_id, tile}]` | Timebar ticks/slider domain |
| `GET /api/meta/status` | — | `{latest_processed_at, warehouse_connected, hex_obs_row_count, parcel_obs_available}` | Live indicator — **now explicitly surfaces whether observation data exists**, so the UI can show a "pipeline still processing" state instead of a silently empty map |
| `GET /api/do-boundary` | — | `{boundary_geojson}` | DO outline layer, fetched once — confirmed working, §0.6 |

**H3 grain:**

| Endpoint | Params | Returns |
|---|---|---|
| `GET /api/hex-cells/geometry` | `season`, `municipio_codes` (required) | `[{h3_cell_id (string!), codigo_municipio, municipio, boundary_geojson}]` — **now includes `boundary_geojson` via server-side `h3_boundaryasgeojson`, see §0.3** |
| `GET /api/hex-cells/values` | `season`, `municipio_codes`, `obs_date?` | `[{h3_cell_id, obs_date, doy, ndvi_mean, ndre_mean, ndmi_mean, n_px_ndvi, n_px_ndre, n_px_ndmi}]` — always all 3 indices |
| `GET /api/hex-cells/{h3_cell_id}/timeseries` | `season` | `[{obs_date, doy, ndvi/ndre/ndmi_mean, n_px_*}]` |

**Parcel grain:**

| Endpoint | Params | Returns |
|---|---|---|
| `GET /api/parcels/geometry` | `municipio_codes` (required) | `[{recinto_id, codigo_municipio, municipio, area_ha, reliability_class, boundary_geojson}]` via `ST_AsGeoJSON(geometry)` |
| `GET /api/parcels/values` | `season`, `municipio_codes`, `obs_date?` | `[{recinto_id, obs_date, doy, ndvi/ndre/ndmi mean+p50(+std), pct_valid, n_px_valid, n_px_total}]` — **depends on `parcel_obs` existing, see §0.1** |
| `GET /api/parcels/{recinto_id}/timeseries` | `season` | full time series incl. `reliability_class` |

**Filter → mechanism map:**

| Control | Round-trip? | Why |
|---|---|---|
| Grain | Yes | Switches endpoint family entirely |
| Vegetation index | **No** | `/values` returns all 3 indices; toggle just recolors client-side |
| Season | Yes | Refetch dates/geometry/values |
| Quality overlay switch | **No** | `reliability_class`/`pct_valid` already in loaded payload |
| Reliability checkboxes | **No** | Client-side filter, mirrors mock's `relFilter` |
| Municipality checkboxes | **Yes** | The one real payload-size lever → `municipio_codes` |
| Reset filters | Yes | Default to a **single small** municipio at H3 grain (see §0.4 — even a mid-size one is 67k cells) |
| Click-select | Yes (small) | `.../{id}/timeseries` only; rest read from loaded state |
| Timebar scrub/play | **Per-`obs_date` fetch is now the default**, see §0.4 | |

**Timebar strategy (revised per §0.4):** H3 res-12 cells are ~0.031 ha each. A single
mid-size municipio (Aranda de Duero, 4,977 parcels) resolves to **67,480 distinct H3 cells**
— measured, not estimated. Default behavior: client always fetches `/hex-cells/values` per
`obs_date` on scrub-release/play-tick, with 1-2-date prefetch during Play and debounced drag;
"fetch all dates once" only kicks in when the computed `n_cells × n_dates` estimate is below
~10,000 rows (revised down an order of magnitude from the first draft's 50-100k), which in
practice means only the smallest municipios or a single already-selected date. Parcel grain
is unaffected (bounded DO-wide at 69,785 parcels), so this distinction matters almost
entirely for H3 grain.

---

## 3. SQL query list

```sql
-- meta/seasons
SELECT season, 'h3' grain, COUNT(DISTINCT obs_date) n_dates FROM hex_obs GROUP BY season
UNION ALL
SELECT season, 'parcel' grain, COUNT(DISTINCT obs_date) n_dates FROM parcel_obs GROUP BY season
ORDER BY season;

-- meta/municipios
SELECT m.codigo_municipio, m.municipio, COUNT(DISTINCT p.recinto_id) n_parcels
FROM ref_municipios m JOIN ref_vineyard_parcels p ON p.codigo_municipio = m.codigo_municipio
GROUP BY m.codigo_municipio, m.municipio ORDER BY m.municipio;

-- meta/dates  (:season; table = hex_obs or parcel_obs by :grain)
SELECT DISTINCT obs_date, doy, item_id, tile FROM hex_obs
WHERE season = :season ORDER BY obs_date;

-- meta/status
SELECT
  (SELECT MAX(processed_at) FROM hex_obs) AS latest_hex_processed_at,
  (SELECT COUNT(*) FROM hex_obs) AS hex_obs_row_count;
  -- parcel_obs equivalents once the table exists

-- do-boundary  (confirmed working)
SELECT ST_AsGeoJSON(geometry) boundary_geojson FROM ref_do_boundary;

-- hex-cells/geometry  (:season, :municipio_codes — normalized/sorted for cache hits)
-- NOW includes boundary_geojson via h3_boundaryasgeojson (confirmed live, §0.3)
WITH target AS (
  SELECT x.h3_cell_id, p.codigo_municipio,
         ROW_NUMBER() OVER (PARTITION BY x.h3_cell_id ORDER BY x.coverage_fraction DESC) rn
  FROM ref_parcel_h3_xwalk x JOIN ref_vineyard_parcels p ON p.recinto_id = x.recinto_id
  WHERE p.codigo_municipio IN (:municipio_codes)
)
SELECT DISTINCT CAST(t.h3_cell_id AS STRING) h3_cell_id, t.codigo_municipio, m.municipio,
       h3_boundaryasgeojson(t.h3_cell_id) boundary_geojson
FROM target t
JOIN ref_municipios m ON m.codigo_municipio = t.codigo_municipio
WHERE t.rn = 1;

-- hex-cells/values  (:obs_date nullable -> all dates; same target CTE as above, no boundary needed)
SELECT CAST(h.h3_cell_id AS STRING) h3_cell_id, h.obs_date, h.doy,
       h.ndvi_mean, h.ndre_mean, h.ndmi_mean, h.n_px_ndvi, h.n_px_ndre, h.n_px_ndmi
FROM hex_obs h JOIN target t ON t.h3_cell_id = h.h3_cell_id AND t.rn = 1
WHERE h.season = :season AND (:obs_date IS NULL OR h.obs_date = :obs_date)
ORDER BY h.obs_date;

-- hex-cells/{id}/timeseries
SELECT obs_date, doy, ndvi_mean, ndre_mean, ndmi_mean, n_px_ndvi, n_px_ndre, n_px_ndmi
FROM hex_obs WHERE h3_cell_id = :h3_cell_id_bigint AND season = :season ORDER BY obs_date;

-- parcels/geometry  (confirmed working against ref_vineyard_parcels/ref_municipios)
SELECT p.recinto_id, p.codigo_municipio, m.municipio, p.superficie_m2 / 10000.0 area_ha,
       p.reliability_class, ST_AsGeoJSON(p.geometry) boundary_geojson
FROM ref_vineyard_parcels p JOIN ref_municipios m ON m.codigo_municipio = p.codigo_municipio
WHERE p.codigo_municipio IN (:municipio_codes);

-- parcels/values  (blocked until parcel_obs exists, §0.1)
SELECT recinto_id, obs_date, doy, ndvi_mean, ndvi_p50, ndvi_std,
       ndre_mean, ndre_p50, ndre_std, ndmi_mean, ndmi_p50,   -- no ndmi_std column
       pct_valid, n_px_valid, n_px_total
FROM parcel_obs
WHERE season = :season
  AND recinto_id IN (SELECT recinto_id FROM ref_vineyard_parcels WHERE codigo_municipio IN (:municipio_codes))
  AND (:obs_date IS NULL OR obs_date = :obs_date)
ORDER BY obs_date;

-- parcels/{id}/timeseries  (blocked until parcel_obs exists, §0.1)
SELECT obs_date, doy, ndvi_mean, ndvi_p50, ndvi_std, ndre_mean, ndre_p50, ndre_std,
       ndmi_mean, ndmi_p50, pct_valid, reliability_class
FROM parcel_obs WHERE recinto_id = :recinto_id AND season = :season ORDER BY obs_date;
```

Two things every query-building function must do: (1) send `h3_cell_id` to the client as a
**string**, never a JSON number — it's a `BIGINT` that exceeds JS's safe-integer range; (2)
normalize `municipio_codes` (sort, uppercase, dedupe, join) before binding, so equivalent
filter selections produce byte-identical query text and hit Databricks' deterministic-query
result cache.

---

## 4. Backend module layout

```
app/
├── app.yaml                      # command + sql-warehouse resource binding
├── requirements.txt              # fastapi, uvicorn, databricks-sql-connector, databricks-sdk, pydantic, cachetools
├── app.py                        # FastAPI() instance; includes routers; mounts frontend/dist LAST (after /api/*)
├── config.py                     # NS constant, warehouse id from env, cache TTLs
├── db.py                         # Config()/sql.connect() wrapper, context-managed per request
├── models.py                     # Pydantic response models (SeasonOut, HexCellValueOut, ParcelGeometryOut, ...)
├── cache.py                      # cachetools.TTLCache wrapper — meta endpoints only
├── routers/
│   ├── meta.py                   # /api/meta/*, /api/do-boundary
│   ├── hex_cells.py              # /api/hex-cells/*
│   └── parcels.py                # /api/parcels/*
└── data_access/
    ├── queries.py                # parameterized SQL builders from §3, one per query
    └── repository.py             # run_query(sql, params) -> list[dict]
```

Route handlers are **plain `def` (sync)**, not `async def` — FastAPI runs sync handlers in
its threadpool automatically, the simplest correct way to call the fully-synchronous
`databricks-sql-connector`/SDK. Routers stay thin (validate params → call `repository` →
return a Pydantic model); only `repository.py` opens a warehouse connection.

---

## 5. Frontend architecture

```
<App>                                  # owns URL-synced filter state (React Router useSearchParams)
  <TopBar />                           # brand, title/subtitle, live-data pill from /api/meta/status
  <StatStrip />                        # 4 tiles, computed client-side from loaded data
  <div class="body">
    <Sidebar>
      <GrainToggle /> <IndexToggle /> <SeasonChips />
      <QualitySection />                # toggle + reliability checklist (parcel) or explainer note (h3)
      <MunicipioChecklist + SelectAllNone />
      <ResetFiltersLink />
    </Sidebar>
    <MapPane>
      <PlotlyMapView />                 # Choroplethmapbox
      <MapMetaPill /> <Legend /> <ZoomControls />
    </MapPane>
    <Inspector />                       # conditional; fetches .../{id}/timeseries on select
  </div>
  <TimeBar />                           # play/pause, slider, ticks from useDates()
```

**State:** URL query params (`grain`, `index`, `season`, `municipio_codes`,
`quality_overlay`, `reliability_filter`, `obs_date`, `selected_id`) drive all data fetching.
Ephemeral view state (`isPlaying`, Plotly's own `mapbox.center`/`zoom`) stays local. Data
fetching via **`@tanstack/react-query`**.

**Plotly map:**
- `react-plotly.js` + full `plotly.js` (not `-basic-dist`; `Choroplethmapbox` needs it —
  ~3-4MB, lazy-load the map component behind the shell to keep first paint fast).
- `layout.mapbox.style = "carto-positron"` / `"carto-darkmatter"` — token-free, switches with
  the same `prefers-color-scheme`/`data-theme` logic the mock's CSS already uses.
- Trace: `Choroplethmapbox`, `locations` = `h3_cell_id`/`recinto_id`, `featureidkey: "id"`,
  GeoJSON built **entirely server-side now** — both grains return `boundary_geojson` in their
  `/geometry` response (§0.3 removed the H3-specific client-side branch that the first draft
  needed).
- Color ramp: resolve `--seq-1..7` CSS vars to literal hex, fixed `zmin`/`zmax` per index
  (NDVI 0.10–0.80, NDRE 0.05–0.55, NDMI −0.10–0.35 — fixed agronomic domains). `showscale:
  false` — legend is a bespoke HTML component.
- Quality overlay: swap `z` for a 0/1 reliability array + 2-stop good/warning colorscale,
  `marker.opacity` array from `pct_valid` — same trace, different props.
- Click-select: `plotly_click` → `points[0].location` is the id → URL state update →
  `.../{id}/timeseries`. Selection highlight via per-feature `marker.line.color`/`width`.
- Zoom/pan: native Plotly mapbox drag/scroll; the mock's hand-rolled SVG viewBox math is
  fully retired. `+/−/reset` buttons wrap `Plotly.relayout`.
- **Empty-data state (new, given §0.1):** the map component must render a clean "no
  observations yet for this season/grain" state when `/values` returns zero rows — not just
  an empty/broken choropleth. Wire this to `/api/meta/status`'s row-count fields.

**Real-data adaptations from the mock:**
- H3 grain's inspector has **no p50/std** (not in `hex_obs`) — show mean + `n_px_<index>`
  instead of the mock's always-present mean/p50/std triple.
- Don't hardcode "res 12" in copy — derive from data.
- "Illustrative sample data" pill → live indicator from `/api/meta/status`, now including a
  distinct "pipeline processing, no observations yet" state (see §0.1).
- Default municipio selection at H3 grain: a **small** municipio (bottom quartile by parcel
  count), not "all" and not even a mid-size one — Aranda de Duero alone is 67,480 cells (§0.4).

---

## 6. Auth & deployment

- **Auth: service-principal (app identity)** — `Config()` + `sql.connect(...,
  credentials_provider=lambda: cfg.authenticate)`. Requires the app SP to have `CAN_USE` on
  the warehouse and `SELECT` on the `geospatial.ribera_duero.*` tables (one-time UC grant).
  On-behalf-of-user auth is explicitly **not** used — single external-buyer demo.
- **`app.yaml`** (illustrative — verify exact schema against current Apps docs at
  implementation time):
  ```yaml
  command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$DATABRICKS_APP_PORT"]
  resources:
    - name: sql-warehouse
      sql_warehouse: { id: <warehouse-id>, permission: CAN_USE }
  env:
    - name: DATABRICKS_WAREHOUSE_ID
      valueFrom: sql-warehouse
  ```
- **Single-port constraint:** FastAPI serves the built SPA itself — `StaticFiles` mount for
  `frontend/dist/assets`, catch-all `GET /{full_path:path}` → `frontend/dist/index.html`,
  registered **after** all `/api/*` routers.

---

## 7. Performance/caching plan

| Data | Cache | TTL | Why |
|---|---|---|---|
| `meta/seasons`, `meta/municipios` | in-process (`cache.py`) | ~1hr / very long | Changes only on rare NB run |
| `meta/dates` | in-process | ~15-30 min | Short cache fine for a demo/analytics app |
| `do-boundary` | in-process | effectively indefinite | Immutable |
| `.../geometry`, `.../values` | **not** cached server-side — relies on warehouse result cache + client TanStack Query cache | — | High-cardinality, avoids staleness/memory bloat |

- Cold start: serverless warehouse is 30-60s. Accept it with a clear "warming up…" state.
- Pre-deploy housekeeping (§0.5): `ANALYZE TABLE ref_vineyard_parcels/ref_municipios COMPUTE
  STATISTICS FOR ALL COLUMNS` — both currently show `missing` optimizer stats.
- Payload minimization: never `SELECT *`; both geometry endpoints now do GeoJSON export
  server-side (`ST_AsGeoJSON` / `h3_boundaryasgeojson`), both bounded by the municipio filter.

---

## 8. Open risks to flag

1. ~~H3-boundary SQL function unconfirmed~~ — **RESOLVED**, confirmed working live (§0.3).
2. ~~No query has been run against these tables yet~~ — **RESOLVED**, this document's §0 is
   the result of doing exactly that.
3. **NEW, blocking — `parcel_obs` doesn't exist and `hex_obs` is empty (§0.1).** The app can
   be fully built and deployed against the schema, but will have nothing to show on the map
   until NB04 (`04_parcel_indices.py`) is re-run to completion for at least one season, and
   ideally NB03 (`03_download_assets.py`) has staged more than the current 60-row/single-season
   COG slice. This is a pipeline-completion dependency, not an app-architecture problem.
4. **SIGPAC licence** (`config/ribera.yml`: `free-use-only-no-commercial`) — already a known,
   tracked issue. Confirm before productizing beyond an internal demo whether the licence
   covers showing it to the Consejo at all vs. redistributing parcel geometry downstream.
5. **H3-cell→municipio assignment is a heuristic** (majority `coverage_fraction`) — a
   boundary-adjacent cell could plausibly be expected in a neighboring municipio's filtered
   view; worth a UI note.
6. ~~`reliability_class` consistency between `ref_vineyard_parcels` and `parcel_obs`~~ —
   **still open, now blocked rather than merely unverified**: cannot cross-check against a
   table that doesn't exist yet. Re-run once `parcel_obs` is populated.
7. **Plotly bundle size** (~3-4MB) — mitigate via lazy-loading the map component.
8. `ARCHITECTURE.md`'s "~60% of parcels / ~10% of area" `aggregate_only` estimate doesn't
   match the measured 25.4% / 2.0% (§0.2) — a documentation correction, not an app risk, but
   worth a one-line fix in that file while it's fresh.

---

## Verification — completed vs. still open

- ~~Run the row-count/EXPLAIN queries against the live warehouse~~ — **done, §0**.
- ~~Spike the H3-boundary question~~ — **done, §0.3, function confirmed working**.
- **Still open (implementation phase):** build backend endpoints against real data with curl
  checks per endpoint before wiring the frontend — for `/api/parcels/values` and
  `/api/hex-cells/values` this will currently return empty payloads until §8.3 is resolved,
  so those two endpoints can only be schema/shape-verified, not data-verified, right now.
- **Still open:** once the frontend is up, manually replay every mock interaction against
  real data — also gated on §8.3 for anything touching vegetation-index values.
