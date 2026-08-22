# Ribera del Duero — Satellite Vineyard Intelligence MVP

**Working name:** `viticultura-eo`
**Version:** 0.1 — 31 July 2026
**Target:** 10 working days
**Platform:** Databricks (Unity Catalog + serverless), GeoBrix, Databricks Apps

---

## 1. What we are building

A Databricks-native pipeline that ingests Sentinel-2 imagery over the DO Ribera del Duero,
reduces it to an H3-indexed multi-year vegetation-index table, and serves it through an
interactive map — plus a clearly-separated experimental layer that backtests whether those
indices predict the DO's annual harvest.

**In one sentence:** seven seasons of Sentinel-2 over 27,468 ha of vineyard, as ~1M H3
cells × ~245M observations, browsable in a map, with a yield-forecast harness attached.

### Why Ribera

| Fact | Value | Source |
|---|---|---|
| Inscribed vineyard | 27,468.59 ha | Consejo Regulador, 2025 |
| 2025 harvest | 129,555,457 kg (2nd largest ever) | Consejo Regulador |
| 2025 average yield | 4,716.50 kg/ha | Consejo Regulador |
| Red varieties | 98.89% of harvest | Consejo Regulador |
| 2026 yield cap (red) | 6,300 kg/ha, cut 10% from 7,000 | BOCyL / plenary, June 2026 |
| Autonomous community | Castilla y León only | — |

Four properties that make it the right first target:

1. **Single CCAA** → one SIGPAC source (ITACyL), one set of conventions.
2. **Near-monovarietal** (98.89% red, dominantly Tempranillo/Tinta del País) → homogeneous signal.
3. **Compact geography** → ~115 municipios in a band along the Duero, cheap to process.
4. **Live commercial tension** → the Consejo cut the cap 10% in June 2026 after a contested
   plenary vote, with no quantitative basis. That is the value story.

---

## 2. Goals and non-goals

### Goals

- **G1** — End-to-end satellite processing on Databricks: STAC → raster → H3 → Delta.
- **G2** — Multi-year (2019–2025) NDVI / NDRE / NDMI at H3 res 12 over the DO.
- **G3** — Interactive H3 map with layer and season selection, deployed as a Databricks App.
- **G4** — Per-parcel (SIGPAC recinto) and per-municipality rollups from the same H3 base.
- **G5** — *(stretch)* Aggregate yield backtest harness with leave-one-year-out validation.
- **G6** — Config-driven so a second DO is a config change, not a code change.
- **G7** — Showcase Databricks geospatial capability end to end.

### Non-goals for v0.1

Explicitly out of scope. Every one of these is a phase-2 item.

- Parcel-level yield prediction (no per-parcel ground truth exists yet).
- Spectral unmixing / row-geometry correction (needed for parcel-level, not for aggregate).
- Thermal, SAR, evapotranspiration, irrigation advice.
- Any commercial-resolution imagery (Planet, Pléiades).
- Disease or pest models.
- User accounts, entitlements, billing, licensing tiers.
- Mobile.
- Anything requiring data from the Consejo Regulador.

**The last one is a hard constraint: v0.1 must be buildable entirely from public data.**
Its purpose is to be the artifact that gets us private data later.

---

## 3. Success criteria

| # | Criterion | Measure |
|---|---|---|
| S1 | Pipeline completes | Full 2019–2025 backfill runs end to end as a job |
| S2 | Mask is correct | Per-municipality `DO_inscribed_ha / SIGPAC_VI_ha` in 0.5–1.0, no municipality > 1.0 |
| S3 | Signal is real | Vineyard cells separate from cereal on the July/April index ratio |
| S4 | Phenology is plausible | Modelled peak DOY within ±10 days of the DO's published *Fechas de vendimia* minus typical lag |
| S5 | App works | Map loads DO extent in < 3 s, zooms to res 12 without stalling |
| S6 | Extensible | A second DO added by editing config only |
| S7 | *(stretch)* Model | LOYO MAPE beats trailing-3-year-mean baseline. **Reporting a failure here counts as success** — the harness is the deliverable |

---

## 4. Reused accelerators

| Asset | Source | How we use it |
|---|---|---|
| **EO Series** | `databrickslabs/geobrix` notebooks | Template for STAC discovery → band read → H3 raster tables → multi-band stacking. Closest single match to our ingestion. |
| **Vapor-Eyes** | `databrickslabs/geobrix` notebooks | Architectural template: screen a satellite signal over a region down to the responsible party. Same shape as signal → parcel → viticultor. |
| **H3 Rasterize** | `databrickslabs/geobrix` notebooks | Polygon → pixel-aligned H3 raster stack. Used for SIGPAC mask → H3 cells. |
| **Helios / PMTiles** | `databrickslabs/geobrix` notebooks | Deferred. Fallback if the SQL-warehouse-backed app can't hold interactive latency. |
| `rasterx` | GeoBrix module | `spark.read.format("gdal")`, `rst_boundingbox`, `rst_width`, `rst_metadata`, raster cataloguing. |
| `gridx` | GeoBrix module | H3 cell functions, point/polygon → cell. |
| `vectorx` | GeoBrix module | JTS/ST geometry ops on SIGPAC vectors. |
| **geospatial-h3-viz-app** | `databricks-industry-solutions` | **Forked as our viewer.** Dash + Leaflet, DABs deploy, on-behalf-of UC auth, viewport-filtered queries, zoom-driven H3 resolution. Requires only a table with an H3 column. |
| **Genie Map** | `databrickslabs/geobrix` apps | Deferred stretch. Natural-language query over the gold table (React + kepler.gl). |

> **Note on GeoBrix maturity:** GeoBrix is in beta. Pin the version in
> `requirements.txt`. Fallback path if a module blocks us: plain `rasterio` +
> `h3-py` inside a Pandas UDF. Slower and less impressive, but unblocks the pipeline.

---

## 5. Decision record

Decisions taken, with reasoning, so we don't relitigate them.

**D1 — H3 res 12 is the storage grid.**
Earlier analysis argued H3 should be an internal key with parcels as the presentation
unit. **Reversed.** Both accelerators are H3-native, and fighting that means rewriting
the parts we're reusing. Res 12 averages ~307 m², which maps to roughly 3 Sentinel-2
10 m pixels and ~1:1 with 20 m bands — the correct resolution for this sensor. Res 13
would oversample; res 11 would blur small parcels.

**D2 — Keep parcel identity throughout.**
Do *not* dissolve recintos into municipality geometries. Maintain an
`h3_cell → recinto_id` lookup so any rollup (parcel, municipality, DO) is a `GROUP BY`.
This costs nothing now and means the day per-parcel yield data arrives, we change a
`GROUP BY` and already have seven years of history.

**D3 — Model at aggregate, not parcel.**
There is no per-parcel ground truth. Aggregate is also *statistically easier*: the
mixed-pixel bias from inter-row soil and cover crop is roughly constant year to year at
DO scale and is absorbed by the regression intercept and slope. At parcel scale that
bias becomes between-parcel variance and dominates the error.

**D4 — Fixed mask across all years.**
Intersection of SIGPAC `VI` recintos across 2019–2025. A mask that changes annually
injects step changes into the time series that the model will happily learn.

**D5 — Purity over coverage.**
Negative buffer 15 m, drop recintos < 0.5 ha. We lose area and keep signal. With
~1M cells we have plenty to spare. The resulting selection bias (toward larger, younger,
trellised parcels, away from old *viñedo en vaso*) is constant given D4 and therefore
calibratable.

**D6 — Sentinel-2 only for v0.1.**
Landsat back-extension to 2013 is the right move for the model's sample size, but
harmonizing two sensors is real work and would blow the two weeks. Phase 2.

**D7 — Every acquisition, filtered per pixel rather than per scene.**
No fixed-cadence subsampling. Choosing scenes at intervals means choosing before knowing
usability, and clear-sky selection correlates with weather, which correlates with yield —
an arbitrary scene rule can inject bias that tracks the target. Filter loosely at STAC
(`eo:cloud_cover < 80`) to save bytes, then mask per pixel with SCL so validity is
per-cell-per-date. Scene count is not the cost driver; bytes are, and windowing already
cut those ~20×. Detail in **NB03**; sidelap deduplication in **NB04**; the regular 5-day
grid is an output of **NB05**, not an acquisition constraint.

---

## 6. Data sources

All public. No agreements, no NDAs, no personal data.

| Source | What | Access | Notes |
|---|---|---|---|
| **Sentinel-2 L2A** | Surface reflectance, 2019–2025 | STAC (see below) | Primary signal |
| **SIGPAC (ITACyL)** | Recinto polygons + land use, annual snapshots since 2012 | `ftp://ftp.itacyl.es/cartografia/05_SIGPAC/` — shapefiles by municipality/province; also IDECyL WFS/WMS | Whole DO is in CyL |
| **DO municipality list** | ~115 municipios | Ribera *pliego de condiciones* / Reglamento, BOCyL | Manual transcription, one-off |
| **Superficie Viñedo por Municipio** | DO inscribed ha per municipality | riberadelduero.es → Estadísticas | **Mask QA + DO-share factor** |
| **Producción de uva y rendimiento** | Annual kg + kg/ha | riberadelduero.es → Estadísticas | Model target |
| **Fechas de vendimia** | Harvest start/end by year | riberadelduero.es → Estadísticas | Free phenology validation |
| **Copernicus DEM GLO-30** | Elevation, slope, aspect | AWS open data | Optional covariate |
| **ERA5-Land / AgERA5** | GDD, rainfall | CDS API | Model features (stretch only) |
| **Neighbouring DO production** | Rueda, Toro, Cigales, Arlanza | Each Consejo's site | **Sample-size fix — see §10** |

### STAC endpoint — pick by cloud

| Workspace cloud | Endpoint | Collection | Notes |
|---|---|---|---|
| Azure | `planetarycomputer.microsoft.com/api/stac/v1` | `sentinel-2-l2a` | Co-located, cheapest egress. Requires SAS signing (`planetary-computer` package). |
| AWS | `earth-search.aws.element84.com/v1` | `sentinel-2-l2a` | Public COGs, no auth, simplest. |

### Bands

| Band | Res | Use |
|---|---|---|
| B04 (red) | 10 m | NDVI |
| B08 (NIR) | 10 m | NDVI |
| B05 (red-edge 705) | 20 m | NDRE |
| B8A (NIR narrow) | 20 m | NDRE, NDMI |
| B11 (SWIR 1610) | 20 m | NDMI |
| SCL | 20 m | Cloud/shadow mask |

```
NDVI = (B08 - B04) / (B08 + B04)
NDRE = (B8A - B05) / (B8A + B05)      # preferred: saturates far later on dense canopy
NDMI = (B8A - B11) / (B8A + B11)
```

> ### ⚠️ Processing baseline offset — do not skip
> Sentinel-2 processing baseline **04.00 (25 Jan 2022)** introduced a `BOA_ADD_OFFSET`
> of **−1000**. Uncorrected, the 2019–2025 series has a step change in the middle of the
> training window. Read `s2:processing_baseline` from each STAC item's properties and
> apply the offset conditionally. **This is the single most likely silent bug in the
> project.** Add an assertion test: mean NDVI over the stable mask must not jump
> discontinuously across Jan 2022.

---

## 7. Architecture

```
                    ┌─────────────────────────────────────┐
   PUBLIC SOURCES   │  ITACyL SIGPAC   Consejo stats      │
                    │  STAC (S2 L2A)   BOCyL municipios   │
                    └──────────────┬──────────────────────┘
                                   │
  ┌────────────────────────────────▼─────────────────────────────────┐
  │ REF   ref.do_config · ref.municipios · ref.sigpac_recintos       │
  │       ref.vineyard_mask · ref.h3_recinto · ref.do_production     │
  └────────────────────────────────┬─────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼─────────────────────────────────┐
  │ BRONZE  stac_items          STAC discovery, cloud-filtered       │
  │         s2_raster_catalog   GDAL reader, windowed to mask bbox   │
  └────────────────────────────────┬─────────────────────────────────┘
                                   │  rasterx → gridx (H3 res 12)
  ┌────────────────────────────────▼─────────────────────────────────┐
  │ SILVER  h3_band_obs         cell × date × band, SCL-masked       │
  │         h3_indices          + NDVI / NDRE / NDMI      ~245M rows │
  │         h3_indices_smooth   Whittaker, regular 5-day DOY grid    │
  └────────────────────────────────┬─────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼─────────────────────────────────┐
  │ GOLD    h3_season_features    per cell × season                  │
  │         recinto_season        area-weighted, + vigour class      │
  │         municipio_season                                         │
  │         do_season                                                │
  │         h3_viz_r{6,8,10,12}   pre-aggregated for the app         │
  │         yield_backtest        stretch                            │
  └────────────────────────────────┬─────────────────────────────────┘
                                   │
  ┌────────────────────────────────▼─────────────────────────────────┐
  │ APP     Databricks App (Dash + Leaflet), DABs-deployed           │
  │         SQL warehouse · on-behalf-of UC auth · viewport queries  │
  └──────────────────────────────────────────────────────────────────┘
```

### Unity Catalog layout

```
catalog: viticultura
  schema: ref       reference / static
  schema: bronze    raw ingest
  schema: silver    cleaned, H3-indexed
  schema: gold      analytics + serving
  volume: viticultura.bronze.raw    downloaded SIGPAC shapefiles, scratch rasters
```

---

## 8. Pipeline stages

> **Where to find the how-to for each stage: see §17, Implementation reference map.**

### NB00 — `00_setup`
Create catalog, schemas, volume. Install pinned GeoBrix. Register `rasterx`, `gridx`,
`vectorx`. Write `ref.do_config` from `config/ribera.yml`. Smoke-test by running the
upstream EO Series notebook unmodified against its Alaska sample.

### NB01 — `01_reference_geometry`
1. Download SIGPAC shapefiles for Burgos, Valladolid, Soria, Segovia from the ITACyL FTP,
   snapshots 2019–2025 → volume.
2. Load to `ref.sigpac_recintos`, partition by year.
3. Filter to the ~115 DO municipios (`ref.municipios`).
4. Filter `uso = 'VI'`. Check the area contribution of `VF`, `VO`, `FV`; if combined
   < 1%, exclude and record the decision. **Never change this later.**
5. Fixed mask (D4): recintos present as `VI` in *every* year 2019–2025.
6. Negative buffer 15 m; drop area < 0.5 ha (D5).
7. Write `ref.vineyard_mask`.

**QA gate (S2):** join to *Superficie Viñedo por Municipio* and compute
`DO_inscribed_ha / SIGPAC_VI_ha` per municipality. Expect 0.5–1.0. Any municipality > 1.0
means the mask is missing vineyard — stop and fix. Persist the ratio as the DO-share
factor; it is needed to scale mask-derived areas to inscribed area.

### NB02 — `02_h3_index_mask`
Following the **H3 Rasterize** pattern: polyfill `ref.vineyard_mask` to H3 res 12,
producing `ref.h3_recinto (h3_cell, recinto_id, municipio_id, coverage_fraction)`.

Expected ~1.0–1.2M cells (~35,000 ha of masked SIGPAC VI ÷ 0.0307 ha per res-12 cell).
Keep `coverage_fraction` — cells on parcel edges are partial and should be weighted, not
dropped.

Liquid-cluster on `h3_cell`.

### NB03 — `03_stac_discovery`
Query the STAC endpoint per municipality bbox, 1 April – 31 October, 2019–2025.
Write `bronze.stac_items` with item id, datetime, MGRS tile, relative orbit, cloud
cover, processing baseline, asset hrefs.

**Take every available acquisition. Do not subsample on a fixed cadence.**

Scene selection at fixed intervals means choosing before you know usability — you would
routinely keep a cloudy 12 June and discard a clear 14 June. It is also not a neutral
filter: clear days correlate with weather, weather correlates with yield, so an arbitrary
scene rule can inject bias that tracks the target.

Filtering is therefore two-stage, and the real filter is **per pixel, not per scene**:

1. **Loose STAC filter** — `eo:cloud_cover < 80`, purely to skip bytes not worth reading.
   Tile-level cloud cover describes a whole 110 km MGRS tile while the DO occupies a small
   slice of it, so a 70%-cloudy tile can be entirely clear over Ribera. A tight filter here
   discards usable data.
2. **SCL mask per pixel** in NB04, producing per-cell-per-date validity.

No scene is ever rejected; cells are. A half-clouded scene still contributes the clear
half of the DO. Cells will have differing observation counts within a season — that is
correct, and the smoother in NB05 handles it.

**Expected volumes**

| | Per season | 2019–2025 |
|---|---|---|
| Nominal acquisitions per ground point | ~43 (up to ~75 in orbit sidelap) | ~300–500 |
| Valid after SCL masking | ~25–45 | ~175–315 |
| STAC items intersecting the DO | ~150–250 | ~1,000–1,750 |

Inland Castilla summers are clear — cloud is not the binding constraint here.

**Required observation density is not uniform.** This matters only if schedule pressure
forces thinning:

| Window | Why density matters | Safe to thin? |
|---|---|---|
| Apr–Jun | Fast green-up, short-lived frost events, cloudiest period. `greenup_doy` and `april_min` both live here | **No** |
| Jul–Aug | Canopy plateau, slowest-changing signal, most redundancy | Yes |
| Sep–Oct | Senescence slope, moderate change | Somewhat |

If the day-6 backfill runs long, thin the summer plateau and never the spring — and thin
**uniformly** (every Nth acquisition date). Never "the clearest scene per month," which
reintroduces exactly the weather-correlated bias described above.

### NB04 — `04_raster_to_h3`
The core stage, adapted from **EO Series**.

For each STAC item:
1. Windowed read via `spark.read.format("gdal")` restricted to per-municipality bboxes —
   **not full MGRS tiles.** ~2,900 km² against ~60,000 km² for 4–6 full tiles, a ~20×
   reduction.
2. Apply the **baseline offset correction** (see §6 warning).
3. Resample 20 m bands to the 10 m grid.
4. Map pixels → H3 res 12 via `gridx`.
5. Inner-join to `ref.h3_recinto` — discard everything outside the mask immediately.
6. Mask on SCL: keep classes 4 (vegetation), 5 (not-vegetated), 6 (water, for QA);
   drop 3 (shadow), 8/9/10 (cloud), 11 (snow).
7. **Deduplicate** — see below.
8. Write `silver.h3_band_obs`.

Then `silver.h3_indices` = NDVI, NDRE, NDMI per cell per date.

> **Sidelap deduplication.** Adjacent MGRS tiles overlap, so the same ground appears in
> multiple STAC items on the same date, sometimes from different relative orbits with
> differing view angles. Reading naively double-counts cells in the sidelap zones,
> inflating row counts and biasing averages toward the overlap. Resolve with
> `GROUP BY (h3_cell, obs_date)` taking the mean of valid observations, and retain
> `n_obs` for diagnostics.

**Byte volume**

| | |
|---|---|
| Windowed area | ~2,900 km² |
| Per full-DO date | ~174 MB (2 bands @ 10 m + 4 bands @ 20 m) |
| Per season | ~7 GB |
| **2019–2025 total** | **~35–50 GB** |
| Naive full-tile equivalent | ~500 GB+ |

Scene count is not the cost driver — bytes are, and windowing has already cut them ~20×.
A few hours of serverless job time, and effectively free on egress if the STAC endpoint
is co-located with the workspace cloud (§6).

**Scale:** ~1M cells × ~35 dates × 7 seasons ≈ **245M rows**. Roughly 10–20 GB Delta.
This is the "massive scale H3" the viz app was built for.

Liquid-cluster on `(h3_cell, obs_date)`.

### NB05 — `05_smooth_phenology`
Per cell per season: Whittaker smoother (or Savitzky-Golay) over the **irregular** real
observation dates, then interpolated onto a regular 5-day DOY grid. Write
`silver.h3_indices_smooth`.

> The 5-day grid is the **output** of this stage, not an input constraint on acquisition.
> Regularization happens after smoothing so that derived features — especially `peak_doy`
> — are comparable across cells and seasons despite differing observation counts.

**QA gate (S3):** compute the July/April NDVI ratio per cell. Vines bud in April and peak
July–August; winter cereal peaks April–May and is bare stubble by July. Flag and drop
cells that behave like cereal. Second filter on amplitude — irrigated maize and beet are
also summer-green but reach NDVI 0.85+, whereas mixed-pixel vineyard rarely clears 0.55.
Filter on **summer-green AND moderate peak**, not summer-green alone.

**QA gate (S4):** compare modelled senescence onset against the DO's published
*Fechas de vendimia*.

### NB06 — `06_season_features`
Per cell per season, from the smoothed series:

| Feature | Definition |
|---|---|
| `peak_ndre`, `peak_doy` | Max value and its day of year |
| `integral_apr_sep` | Area under the curve, 1 Apr – 30 Sep |
| `veraison_value` | Mean over DOY 200–225 |
| `greenup_doy`, `greenup_rate` | Inflection on the rising limb |
| `senescence_slope` | Slope on the falling limb |
| `april_min` | Minimum, DOY 91–151 — **frost detector** |
| `cv_within` | Coefficient of variation across cells within the parent recinto |

Roll up to `gold.recinto_season` (area-weighted by `coverage_fraction`),
`gold.municipio_season`, `gold.do_season`.

Add `vigour_class`: 5 quantile bins of `integral_apr_sep` within each season. This is
unsupervised, needs no ground truth, and is the zoning product — sellable on its own.

### NB07 — `07_viz_tables`
Materialize `gold.h3_viz_r12` and pre-aggregated parents at res 10, 8, 6 (via
`h3_toparent`). Schema:

```
h3_cell STRING, resolution INT, season INT, layer STRING, value DOUBLE,
municipio_id STRING, recinto_count INT
```

`layer ∈ {ndvi_peak, ndre_peak, ndmi_min, integral, vigour_class, yield_pred}`.

Pre-aggregating rather than rolling up at query time is what keeps the app interactive.
Liquid-cluster on `(resolution, season, layer, h3_cell)`.

### NB08 — `08_yield_backtest` *(stretch — see §10)*

---

## 9. The app

Fork `databricks-industry-solutions/geospatial-h3-viz-app`.

**Inherited as-is:** Dash + Leaflet + Bootstrap, DABs deployment, on-behalf-of Unity
Catalog auth, SQL-warehouse-backed viewport-filtered queries, zoom-driven H3 resolution.

**Changes required:**

1. **Aggregation.** Upstream counts rows with a logarithmic colour scale. We need
   `AVG(value)` with a linear or diverging scale.
2. **Layer selector.** Dropdown over `layer`.
3. **Season selector.** Slider over `season` (2019–2025).
4. **Resolution mapping.** Retune for a DO-sized extent rather than global:

   | Zoom | H3 res | View |
   |---|---|---|
   | 8–10 | 6 | Whole DO |
   | 11–12 | 8 | Municipality group |
   | 13–14 | 10 | Municipality |
   | 15+ | 12 | Parcel |

5. **Parcel overlay.** At zoom ≥ 15, draw `ref.vineyard_mask` boundaries over the hexes.
6. **Popup.** `recinto_id`, municipio, ha, layer value, season.
7. **Colour ramps.** Diverging for anomaly layers, sequential for absolute.

Deploy with `databricks bundle deploy --target dev`.

---

## 10. Yield backtest (stretch)

**Framing, stated up front:** with a single DO and seven seasons this is **n = 7**. That
is not enough to fit a model. This stage delivers a *validated harness* and an honest
answer, not a production forecaster.

### The sample-size fix, which is also requirement G6

Because the pipeline is config-driven, adding a DO is a config row plus a municipality
list. Add **Rueda, Toro, Cigales and Arlanza** — all in Castilla y León, all on the same
ITACyL SIGPAC source, all publishing annual production.

That gives a **5 DO × 7 season panel = 35 observations**. Still small, but enough for a
2–3 feature ridge with year effects. Note the pleasing property: *the thing that makes
the model fittable is the same thing that proves extensibility.* Do this even if the
model fails.

### Setup

- **Target:** `kg/ha`, never total kg. Inscribed surface changes annually; multiply back
  at the end.
- **Features (ruthlessly few):** `integral_apr_sep`, `veraison_value`, `april_min`,
  previous season's yield. Add ERA5 GDD and rainfall-by-window only if the first four
  underperform.
- **Model:** ridge or lasso. **Not** gradient boosting — at n = 35 it will memorize.
- **Validation:** leave-one-year-out, blocked by year. Random k-fold will produce a
  beautiful and entirely fictional R². The business question is "predict a year you have
  never seen."
- **Forecast date:** freeze features at **1 July**. Lead time is worth more commercially
  than accuracy — a July number precedes cap-setting, green-harvest decisions and price
  negotiation.
- **Baseline to beat:** the DO's own trailing 3-year mean yield.

### Known bias to document, not fix

DO-declared production is **censored from above**. Growers over the cap green-harvest, or
the excess is declassified and never enters DO production. The truncation lands precisely
on the high-yield years we most want to predict. Phase 2 mitigates this by modelling
uncensored provincial yield from MAPA/ESYRCE and carrying the cap as a feature. For v0.1,
document it in the results.

### Output

`gold.yield_backtest (do_id, season, predicted_kg_ha, lower, upper, actual_kg_ha, baseline_kg_ha)`
plus one chart: predicted vs actual, LOYO, per DO.

**If it does not beat baseline, say so plainly in the README.** A negative result found
in three days for zero euros is the correct outcome of an MVP.

---

## 11. Repository layout

```
viticultura-eo/
├── databricks.yml                  # DAB root
├── config/
│   ├── ribera.yml                  # DO config — the extensibility seam
│   ├── rueda.yml                   # proves G6
│   └── municipios/ribera.csv       # INE codes from the pliego
├── notebooks/
│   ├── 00_setup.py
│   ├── 01_reference_geometry.py
│   ├── 02_h3_index_mask.py
│   ├── 03_stac_discovery.py
│   ├── 04_raster_to_h3.py
│   ├── 05_smooth_phenology.py
│   ├── 06_season_features.py
│   ├── 07_viz_tables.py
│   └── 08_yield_backtest.py
├── src/viticultura/
│   ├── config.py                   # DO config loader
│   ├── sigpac.py                   # ITACyL download + mask build
│   ├── stac.py                     # discovery + baseline-offset handling
│   ├── indices.py
│   ├── phenology.py                # smoothing + feature extraction
│   └── qa.py                       # the S2/S3/S4 gates as assertions
├── app/                            # fork of geospatial-h3-viz-app
├── resources/
│   ├── jobs.yml                    # backfill + incremental
│   └── app.yml
└── tests/
```

### The extensibility seam

```yaml
# config/ribera.yml
do_id: ribera_del_duero
name: DO Ribera del Duero
ccaa: castilla_y_leon
sigpac_source: itacyl
provinces: [burgos, valladolid, soria, segovia]
municipios_file: config/municipios/ribera.csv
sigpac_uses: [VI]
mask:
  buffer_m: -15
  min_area_ha: 0.5
  fixed_years: [2019, 2020, 2021, 2022, 2023, 2024, 2025]
h3_resolution: 12
season:
  start_doy: 91      # 1 Apr
  end_doy: 304       # 31 Oct
indices: [ndvi, ndre, ndmi]
yield_caps:
  2025: 7000
  2026: 6300
```

Adding a DO = one YAML file plus a municipality CSV. Nothing else.

---

## 12. Ten-day plan

| Day | Work | Gate |
|---|---|---|
| 1 | Env, UC catalog, GeoBrix pinned, both repos cloned. Run upstream EO Series unmodified against its Alaska sample. | GeoBrix works on this workspace |
| 2 | NB01: SIGPAC download, municipality filter, mask build | **S2** — DO-share ratio in range |
| 3 | NB02: H3 polyfill, `ref.h3_recinto`, ref tables | ~1M cells, sane cell/parcel counts |
| 4 | NB03 + NB04 for **2025 only** — one season end to end | One season lands in silver |
| 5 | Fix whatever day 4 broke. Baseline-offset assertion. | Offset test passes |
| 6 | Backfill 2019–2024 as a job | 245M rows in silver |
| 7 | NB05 + NB06: smoothing, features, rollups | **S3, S4** — cereal separation, phenology sane |
| 8 | NB07 + app fork, layer/season selectors, DABs deploy | **S5** — app loads and zooms |
| 9 | NB08 backtest + add Rueda/Toro/Cigales/Arlanza configs | **S6, S7** |
| 10 | QA, README, demo script, screenshots | Demoable |

**Highest-risk stretch is days 4–6.** If it slips, cut the backfill to 2022–2025 and
keep the app; the map is the guaranteed deliverable, the model is not.

---

## 13. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Baseline offset uncorrected** | High | Severe, silent | Assertion test across Jan 2022. Listed first because it fails quietly. |
| GeoBrix beta blocks a stage | Medium | High | Pin version; fallback to `rasterio` + `h3-py` in a Pandas UDF |
| Egress cost / slow reads | Medium | Medium | Co-locate STAC with workspace cloud; windowed reads only; cloud-filter at STAC |
| Mask quality | Medium | High | S2 gate is quantitative and cheap. Spot-check 50 recintos against PNOA WMS |
| n = 7 defeats the model | **High** | Low if framed | Multi-DO panel; report honestly; harness is the deliverable |
| Censored target | High | Medium | Document; phase-2 uses uncensored provincial yield |
| App latency at 245M rows | Low | Medium | Pre-aggregated parent resolutions; PMTiles via Helios as fallback |
| Municipality list transcription | Medium | Medium | Cross-check total against 27,468 ha |

---

## 14. Databricks capabilities showcased

Requirement G7, made explicit:

- **Unity Catalog** — three-level namespace, volumes, lineage, and on-behalf-of auth in
  the app so users only see data they're entitled to.
- **Delta Lake medallion** with liquid clustering on `h3_cell`.
- **GeoBrix** `rasterx` GDAL reader, `gridx` H3 functions, `vectorx` JTS ops.
- **Distributed raster → H3** — the Spark scale-out story, 245M rows from raw imagery.
- **Serverless jobs** for backfill and incremental refresh.
- **Databricks SQL warehouse** serving interactive viewport queries at that scale.
- **Databricks Apps** — Dash app deployed via **Asset Bundles**.
- *(stretch)* **MLflow** for the backtest; **Genie** for natural-language querying.

---

## 15. Phase 2 backlog

Ordered by value, not effort.

1. **Landsat back-extension to 2013** — takes the model from n=35 to n≈120. Biggest single
   statistical win available.
2. **Uncensored target** from MAPA Anuario / ESYRCE provincial yields.
3. **PNOA row geometry + LiDAR canopy height** → spectral unmixing → the vine-only signal.
   This is the technical moat and the prerequisite for anything parcel-level.
4. **Per-parcel yield** once Consejo delivery data is obtained.
5. **Thermal** (Landsat TIRS, ECOSTRESS) → CWSI and ET → the water product.
6. **Sentinel-1 SAR** → cloud-free monitoring, harvest-date detection, frost and hail.
7. Frost/hail anomaly alerting — easier than yield, immediate insurer value.
8. Additional DOs beyond Castilla y León (requires per-CCAA SIGPAC adapters).
9. Genie Map natural-language layer.

---

## 16. Open questions

- Exact municipality list for Ribera — needs transcription from the BOCyL pliego,
  including partial municipalities defined by paraje.
- Does the Consejo publish production **by municipality**, or only DO totals? If by
  municipality, sample size jumps from 7 to ~800 and §10 changes completely. **Check this
  first — it is the highest-leverage unknown in the project.**
- ITACyL FTP snapshot completeness for 2019–2021.
- Whether GeoBrix's H3 polyfill handles `coverage_fraction` natively or we compute it.

---

## 17. Implementation reference map

Where to look up the *how* for each stage. Two GitHub folders carry almost all of it:

- **`notebooks/examples/eo-series`** — the ingestion mechanics
- **`notebooks/examples/vapor-eyes`** (+ `/lakeflow`) — the end-to-end shape and production packaging

### Read these two first, in this order

**1. EO Series** — teaches the mechanics you'll use in every stage: `StacClient`, the
`tile` column abstraction, `rst_h3_tessellate`, and the `(cellid, date)` join key that
everything hangs off. Run it unmodified on day 1 before writing any of our code.

**2. Vapor-Eyes** — teaches the *shape*. Its one-line summary is *"from a wide-area screen
down to the operator whose well pad is leaking."* Ours is the same sentence with the nouns
swapped: from a DO-wide vegetation screen down to the recinto and its viticultor. Read
notebooks 01→05 as an architectural argument, not for the methane.

Everything else is a lookup.

### Stage-by-stage

| Our stage | Reference | What to take from it |
|---|---|---|
| **NB00** setup | EO Series `config_nb.ipynb` | Copy nearly wholesale. Installs the wheel, selects tier, registers functions and readers, sets UC catalog/schema, creates the Volume ETL tree, instantiates `stac_client`, defines `set_conf_safe()` and `FORCE_REBUILD`. Change the `alaska` tree to `ribera`. |
| **NB01** SIGPAC geometry | EO Series `01` — `shapefile_gbx` reader | Reads zipped shapefiles **straight from a `.zip` blob in a Volume, no unzipping**. Exactly the ITACyL download. Vapor-Eyes does the same for EIA plays / TIGER counties as context geometry. |
| **NB01** mask build | Databricks built-in ST | `st_transform` → `st_buffer` → `st_area`. **Reproject to EPSG:25830 first** — a −15 m buffer is meaningless in degrees. |
| **NB02** polygon → H3 | **H3 Rasterize** notebook; `h3_tessellateaswkb` | H3 Rasterize converts polygons into a pixel-aligned multi-band H3 stack — the closest match. EO Series `01` also tessellates an AOI polygon to H3 cells. `h3_boundaryasgeojson` for round-tripping cells to map geometry. |
| **NB03** STAC discovery | EO Series `01. Search STACs.ipynb` | Near-verbatim. Tessellate the AOI to coarse H3, then `stac_client.search(df_cells, geojson_col="geojson", collections=["sentinel-2-l2a"], ...)` fans out one search task per cell. Output is one row per `(cell, item, asset)` into a timestamped Delta dir. Use a coarser AOI tessellation than their res 2 — Ribera is far smaller than Alaska. |
| **NB04** download | EO Series `02. Download STACs.ipynb` | `stac_client.download(band_rows, out_dir, asset_names=[band], ...)`, one task per `(item_id, asset_name)`. Returns `is_out_file_valid`. **`stac_client.repair("band_b04")`** re-downloads failures and Delta-MERGEs them back — this is your throttling defence. |
| **NB04** windowed read | Vapor-Eyes `02` | Downloads B11/B12 "**windowed to the cell**" rather than whole tiles. This is our 20× byte reduction, already demonstrated. |
| **NB04** raster → H3 | EO Series `03. Gridded EO Data.ipynb` | `gtiff` reader materializes a typed `tile` column (bytes, bbox, SRID, nodata) in one pass; `rst_h3_tessellate` shreds each scene into H3 cells, producing per-band Delta tables that "join cleanly across bands and dates." |
| **NB04** index computation | Vapor-Eyes `02` — `gbx_rst_mapalgebra` | Computes `(B11 − B12)/(B11 + B12)`. **Identical normalized-difference form to NDVI/NDRE/NDMI** — change the band pair and you're done. |
| **NB04** band stacking | EO Series `04` — `rst_frombands` | Joins per-band H3 tables on `(cellid, date)` and stacks into one multi-band tile. Use if you'd rather stack then compute than compute per pair. |
| **NB04** clip to AOI | EO Series `04` / Vapor-Eyes `03` — `rst_clip` | Pass cutlines as **EWKB** via `st_asewkb` so the SRID travels with the bytes and reprojection happens automatically. Plain WKB is assumed already in the raster CRS and is *not* reprojected. |
| **NB05** smoothing | *No accelerator* | The one stage with no reference. Whittaker/Savitzky-Golay in a pandas UDF grouped by `h3_cell`. EO Series `03`'s `rst_apply` escape-hatch shows the raster→timeseries projection idea, but the smoother is ours. |
| **NB06** season features | Vapor-Eyes `hotspot_persistence` | No direct code, but this is the analogous "derive chronic-vs-transient behaviour from a per-cell time series" pattern. Otherwise plain Spark aggregation. |
| **NB07** viz tables | `h3_toparent`; Vapor-Eyes gold tables | `h3_toparent` builds the res 10/8/6 rollups from res 12. Vapor-Eyes' `hotspot_latest` / by-play / by-county rollups are the template for map-ready gold. |
| **NB07** PMTiles *(fallback)* | Vapor-Eyes `05`; **Helios** | `gbx_st_asmvt_pyramid` UDTF encodes tile-local MVTs across a zoom range, then `gbx_pmtiles_agg` folds them into one `.pmtiles` v3 archive — pan/zoom in a browser, **no tile server**. |
| **NB08** backtest | *No geospatial reference* | Standard MLflow + sklearn. **But read Vapor-Eyes' "The leaderboard, done defensibly"** — they explain why they rank by detection count rather than summed emission rate (summing instantaneous rates double-counts). It is the right methodological model for how to caveat our censored yield target honestly. |
| **App** | `geospatial-h3-viz-app`; Genie Map | The fork, plus Genie Map as the natural-language stretch. |
| **AI/BI dashboard** | Vapor-Eyes `/lakeflow` | Four-page AI/BI dashboard on **native-geometry map widgets with H3 hexagon choropleths**, DAB-packaged. Far less work than the Dash fork — see §9 note. |
| **Job packaging** | Vapor-Eyes `/lakeflow` | Lakeflow Declarative Pipeline + Auto Loader bronze ("one row per file, lifting dates/IDs out of each filename, so re-runs pick up only new data") + AI/BI, as a DAB on serverless. The production reference for `resources/jobs.yml`. |
| **Per-feature raster clip** *(phase 2)* | **xView** notebook | Clips rasters per labeled object. The pattern for per-recinto raster extraction when we go parcel-level. |

### Naming note

The EO Series docs use Python bindings (`rx.rst_*`); the Vapor-Eyes docs write the
SQL-registered form (`gbx_rst_*`). Same functions, two surfaces. Check
`config_nb.ipynb`'s registration cell for which names are live in your session.

### Gotchas worth memorizing

Four traps that will cost you a day each if you meet them cold:

1. **Serverless repartitioning** — a number-only `repartition(N)` is round-robin and gets
   coalesced back toward one partition by AQE, i.e. silently serial. **Always hash by a
   column:** `df.repartition(N, "h3_cell")`. This is the difference between a 3-hour and
   a 30-hour backfill.
2. **SRID awareness** — Sentinel-2 arrives in UTM, so Ribera is EPSG:32630, not 4326.
   Reproject bboxes before plotting or joining to lat/lon geometry.
3. **EWKB for cutlines** — `st_asewkb`, not `st_aswkb`, or `rst_clip` silently skips
   reprojection.
4. **Planetary Computer throttling** — the free tier returns a ~550-byte XML error body
   that looks like a file. `StacClient` catches it via rasterio read-validation and marks
   `is_out_file_valid = false`; `repair()` cleans up. EO Series scopes itself to *one
   county* to stay inside the limits, and we are pulling ~6,000–10,500 assets — budget for
   several repair passes, or use AWS Earth Search instead (no auth, no throttling).

---

## Appendix A — Sources

- [Ribera del Duero — Estadísticas](https://riberadelduero.es/en/node/2471)
- [Ribera del Duero — Vendimia 2025](https://www.riberadelduero.es/en/node/2845)
- [Agrodiario — 129.5M kg, second largest harvest](https://www.agrodiario.com/articulo/vino-cava-y-otras-bebidas/ribera-duero-cierra-segunda-mayor-cosecha-historia-129-millones-uva/20251203062449067485.html)
- [Agrodigital — 10% yield reduction to 6,300 kg/ha](https://www.agrodigital.com/2026/06/26/ribera-duero-reduccion-rendimiento-uva-tinta/)
- [Vinetur — Ribera abre una batalla por recortar la vendimia](https://www.vinetur.com/20260616102709/ribera-del-duero-abre-una-batalla-por-recortar-la-vendimia.html)
- [ITACyL — SIGPAC download](https://www.itacyl.es/en/agro-y-geo-tecnologia/descarga-datos-geograficos/sigpac)
- [JCyL — SIGPAC data and services](https://cartografia.jcyl.es/web/es/datos-servicios/sigpac.html)
- [SIGPAC land use codes](https://sigpac-hubcloud.es/html/listCod/listados/usos-sigpac.html)
- [GeoBrix — Examples overview](https://databrickslabs.github.io/geobrix/docs/examples/overview)
- [GeoBrix — EO Series notebooks](https://databrickslabs.github.io/geobrix/docs/notebooks/eo-series) · [source](https://github.com/databrickslabs/geobrix/tree/main/notebooks/examples/eo-series)
- [GeoBrix — Vapor-Eyes](https://databrickslabs.github.io/geobrix/docs/notebooks/vapor-eyes) · [notebooks](https://github.com/databrickslabs/geobrix/tree/main/notebooks/examples/vapor-eyes) · [lakeflow bundle](https://github.com/databrickslabs/geobrix/tree/main/notebooks/examples/vapor-eyes/lakeflow)
- [GeoBrix — H3 Rasterize](https://databrickslabs.github.io/geobrix/docs/notebooks/h3-rasterize)
- [GeoBrix — Helios / PMTiles](https://databrickslabs.github.io/geobrix/docs/notebooks/helios)
- [GeoBrix — Clipping (xView)](https://databrickslabs.github.io/geobrix/docs/notebooks/xview)
- [GeoBrix — Genie Map app](https://databrickslabs.github.io/geobrix/docs/examples/genie-map)
- [GeoBrix — Execution tiers](https://databrickslabs.github.io/geobrix/docs/api/execution-tiers)
- [GeoBrix — StacClient API](https://databrickslabs.github.io/geobrix/docs/api/stac)
- [geospatial-h3-viz-app](https://github.com/databricks-industry-solutions/geospatial-h3-viz-app)
- [Vinetur — Rioja's platform (competitive context)](https://www.vinetur.com/20260723104705/rioja-activa-una-plataforma-digital-para-anticipar-la-cosecha-parcela-a-parcela.html)
