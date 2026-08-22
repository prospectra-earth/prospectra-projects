# NB04 `04_parcel_indices` — Plan

**Status:** planned, not built.
**Decision:** H3 is **skipped for v0.1**. The pipeline is parcel-native. See §7.

**In:** `ref_vineyard_parcels` (69,785 parcels) + `s2_assets` (staged windowed COGs from NB03)
**Out:** `parcel_obs` — one row per (parcel, date): NDVI / NDRE / NDMI + validity stats

NB03 stages the pixels; this one turns them into per-parcel measurements.

**Superseded by NB03:** the windowing, retry and validation this plan described are now done by
`StacClient.download(bbox=...)`. NB04 reads local files with `gtiff_gbx` — no remote reads, no
hand-rolled range requests.

### Measured on the NB03 smoke test — two things NB04 must handle

1. **NDVI blows past 1.** Observed p98 = **2.27** on a real September scene. Where atmospheric
   correction drives red near −0.1 and NIR stays positive, `nir+red` approaches zero and the
   ratio explodes. **SCL does not catch these** — the offending pixels were classified 4/5
   (vegetation/bare), so masking alone is not enough. Guard `|nir+red| < 1e-6` **and** clip to
   [−1, 1]. This is measured, not hypothetical.
2. **Per-item coverage varies hugely.** Two items over the same bbox produced 0.17 MB and
   1.37 MB — an 8× difference from partial swath overlap. `pct_valid` per parcel per date is
   therefore a first-class output, not a diagnostic afterthought.

**Radiometry confirmed end to end:** decoded median red 0.146 / NIR 0.242 → NDVI 0.234, against
**0.154 if the offset were dropped** — a 34% understatement at the median, on real data.

---

## 0. Prerequisite: fold buffering into NB01

Agreed — with H3 gone, the buffered geometry belongs upstream. Add to `ref_vineyard_parcels`:

| Column | Meaning |
|---|---|
| `geom_b05 GEOMETRY(4258)` | −5 m buffer — pure-pixel extent for the **10 m** bands (NDVI) |
| `geom_b10 GEOMETRY(4258)` | −10 m buffer — pure-pixel extent for the **20 m** bands (NDRE, NDMI) |
| `area_b05_m2`, `area_b10_m2` | measured in 25830 |
| `min_width_b05_m`, `min_width_b10_m` | `2 × area / perimeter` of the remnant |
| `has_pure_pixel_10m`, `has_pure_pixel_20m` | remnant ≥ one whole pixel wide |
| `reliability_class` | `'parcel'` or `'aggregate_only'` |

Rationale (measured, §2): a pixel centre must sit half a pixel from the boundary, so the buffer is
per band resolution, not a single number. Spec D5's −15 m is the correct value for 30 m Landsat
pixels, not Sentinel-2. Parcels buffered to empty are **flagged, not deleted** — they are ~60% of
parcels but ~10% of area, and dropping them would bias every municipality total toward large estates.

---

## 0b. Tooling: GeoBrix `pyrx`, not raw rasterio — **verified working on serverless**

Use GeoBrix's `rst_*` API throughout. Raw rasterio calls only where GeoBrix has no equivalent.

### The tier fact that decides everything

| Tier | Module | Runs on serverless? |
|---|---|---|
| **Lightweight** | **`pyrx`** | **Yes** — serverless, standard/shared, ARM, Lakeflow |
| Heavyweight | `rasterx` | **No** — needs a JAR + GDAL init script on classic clusters |

**Both tiers implement every `rst_*` function under identical names** — switching is a one-line import
change. So "prefer GeoBrix" and "must run on serverless" are not in tension: we use the same API, via
`pyrx`. Confirmed: **127 `rst_*` functions** in the light tier.

Heavyweight-only, and therefore unavailable to us: the generic `spark.read.format("gdal")` reader
(spec §8 NB04 step 1 assumes it — **that step must be rewritten**) and the vector OGR readers
(already worked around in NB01 with GeoPandas, per A-39).

### Install — PEP 723 header, **not** `%pip`

`%pip install` **fails**: the `[light]` extra pulls ~20 packages (rasterio, scikit-image, netcdf4,
rio-tiler, pyogrio, pyproj, h3, pmtiles…) and the serverless notebook install times out at 80 s —
measured, `TimeoutException: Futures timed out after [80 seconds]`.

Declare it in the notebook's script header instead. This works for both the UI and job runs, and ran
clean in ~2 minutes:

```python
# /// script
# dependencies = [
#   "geobrix[light] @ file:///Volumes/geospatial/ribera_duero/raw/wheels/geobrix-0.4.3-py3-none-any.whl",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
```

Keep the quoted `geobrix[light] @ file://…` PEP 508 form. Putting `[light]` on the path
(`'/Volumes/…whl[light]'`) fails on serverless — pip reads it as part of the filename.

**Wheel is already staged** at `/Volumes/geospatial/ribera_duero/raw/wheels/geobrix-0.4.3-py3-none-any.whl`
(v0.4.3, 29 Jul 2026, from GitHub releases — GeoBrix is **not** on PyPI; the `geobrix` and `rasterx`
PyPI names are 0.0.0 "Coming soon" placeholders). Pin this version; GeoBrix is beta.

```python
from databricks.labs.gbx import pyrx as rx
from databricks.labs.gbx.pyrx import functions as F
rx.assert_rasterio_available(); rx.configure_gdal_env()
```

### 🟢 All three indices are built-in

`indices.builtin_formulae()` returns: **`ndvi`, `ndvi_re`, `ndmi`**, `gndvi`, `msavi`, `ndsi`.

| Our index | GeoBrix formula |
|---|---|
| NDVI | `ndvi` |
| **NDRE** | **`ndvi_re`** |
| **NDMI** | **`ndmi`** |

No hand-rolled band math — `rst_index(tile, 'ndvi_re', band_map)`, with `rst_ndvi` as a shortcut.

### The functions NB03 actually uses

| Step | GeoBrix function |
|---|---|
| Open remote COG | `rst_fromfile` — **verified against a live `sentinel-cogs` S3 URL** |
| Inspect | `rst_srid`, `rst_width`, `rst_height`, `rst_getnodata`, `rst_metadata`, `rst_boundingbox` |
| Window / cut | `rst_clip` |
| 20 m → 10 m | `rst_resample_to_res` |
| Band math | `rst_index`, `rst_ndvi`, `rst_mapalgebra` |
| Stack bands | `rst_frombands` |
| Zonal stats | `rst_rasterize_agg` (+ `agg.rasterize_features`) |
| Aggregates | `rst_summary`, `rst_histogram`, `rst_avg`, `rst_median`, `rst_min`, `rst_max`, `rst_pixelcount` |
| *(if H3 revived)* | `rst_h3_rastertogridavg` / `rst_h3_tessellate` — one call, no bridge table needed |

That last row is worth noting: reviving H3 later is `rst_h3_rastertogridavg`, not a rewrite. It makes
the §7 decision cheaper to reverse than I first assessed.

---

## 1. Verified facts this plan rests on

All measured against the live workspace and the real COGs, not assumed.

### 1.1 🔴 The DO is effectively one tile, and two tiles are useless

Parcels intersecting each tile's observed footprint:

| Tile | lat range | Parcels | % of DO | ha |
|---|---|---|---|---|
| **MGRS-30TVM** | 41.46 – 42.45 | **68,152** | **97.7%** | 27,738 |
| MGRS-30TVL | 40.56 – 41.55 | 6,593 | 9.4% | 2,062 |
| MGRS-30TUM | 41.44 – 42.45 | 5,830 | 8.4% | 6,051 |
| MGRS-30TUL | 40.54 – 41.55 | 728 | 1.0% | 474 |
| **MGRS-30TWL** | 41.49 – 41.55 | **0** | **0%** | — |
| **MGRS-30TWM** | 41.49 – 42.45 | **0** | **0%** | — |

**30TVM alone carries 97.7% of parcels.** 30TWL and 30TWM carry **none**. Restricting reads to the
four productive tiles removes ~1/3 of the catalogue at zero cost to coverage.

The MGRS trailing letter is a **latitude band** — `L` spans ~40.5–41.55, `M` spans ~41.44–42.45. The
DO (41.39–41.90) straddles the boundary, which is why the northern `M` tiles dominate.

### 1.2 Correction to an earlier claim of mine

I said the hardcoded-bbox fix recovered "~20% of the denomination, the Soria end." That is true of the
**boundary's east–west extent** and false of the **vineyard**. Measured:

| | |
|---|---|
| Parcels east of the old bbox edge (−3.2) | **2,920** |
| Area | **357 ha — 1.16%** |
| Municipalities affected | 2 |

Easternmost parcel sits at −3.0006; the boundary runs to −2.8942. The ~11 km beyond contains no
registered vineyard in our mask — because the stale 2014 boundary is missing exactly the 13 Soria
municipalities that would live there. Boundary staleness and the tile gap are the same defect
appearing twice.

The fix was still right — 2,920 parcels would have silently had no imagery — but it is a 1% recovery,
not 20%.

### 1.3 COG structure and read behaviour (measured on a real scene)

```
S2C_30TVL_20250407_0_L2A / B04.tif
  CRS            EPSG:32630          shape 10980 x 10980   uint16
  internal tiles 1024 x 1024         overviews [2, 4, 8, 16]
  bounds         399960, 4490220, 509760, 4600020
  open latency   0.5 s
```

- Tiled with overviews ⇒ genuine windowed range reads work.
- **All six tiles are UTM zone 30N ⇒ EPSG:32630.** One target CRS for rasterisation.
- Measured directly with rasterio for speed; GeoBrix's light tier wraps the same rasterio/GDAL build,
  and `rst_fromfile` was separately verified against a live `sentinel-cogs` URL on serverless.

### 1.4 🔴 An out-of-range window returns empty, silently

Requesting Roa's bbox (41.65–41.73 N) from tile 30TVL (max 41.55 N) returned an array of shape
**`(0, 1005)`** with **no exception**. A parcel that does not intersect the scene yields an empty
read that looks exactly like a valid read of nothing.

**Every read must assert the window intersects `rst_boundingbox(tile)` and returns a non-empty grid.** This is
the same class of defect as the c1-l2a zero-result and the hardcoded bbox: absence presenting as fact.

### 1.5 `proj_epsg` is NULL throughout `stac_items`

The property is `proj:code` in the current STAC spec, not `proj:epsg`, and my `or` fallback captured
neither. **Read the CRS from the opened raster (`src.crs`), never from the catalogue.** Optionally
backfill the column in NB02; not load-bearing either way.

### 1.6 Byte budget — the spec's "20×" needs restating

| Strategy | Area read per date | Note |
|---|---|---|
| All 6 tiles, full extent | ~72,600 km² | the spec's baseline |
| 4 productive tiles, DO window | ~4,950 km² | **~15×** |
| Per-municipality bboxes (82) | 4,364 km² | only **1.5×** better than the above |
| Actual vineyard | **307 km²** | unreachable — see below |

Per-municipality windowing buys almost nothing beyond the tile-level window, because GDAL reads whole
1024×1024 blocks (**10.24 km square at 10 m**) and municipality bboxes are smaller than that and
overlap heavily. Vineyard is only 307 km² scattered across 4,364 km² of bboxes — a 14× residual waste
that block granularity makes unrecoverable.

**Design consequence: window once per (item, band) to the DO extent within that tile. Do not window
per municipality.** Simpler, fewer requests, ~same bytes.

Estimated total: ~300 MB per date (2 bands @10 m + 4 @20 m) × ~294 dates ≈ **~40 GB** for
2022–2025 — which lands inside the spec's 35–50 GB estimate by a different route.

### 1.7 Sidelap may be a smaller problem than the spec assumed

Sampling 4,000 parcels against one representative footprint per tile: **100% intersect exactly one
tile.** Spec NB04's `GROUP BY (cell, date)` deduplication may be largely unnecessary here.

**Caveat:** this used one footprint per tile. Footprints vary per date on partial-swath passes, so
overlap must be re-tested per (date, parcel) before the dedup step is dropped. Keep the `GROUP BY`;
just expect it to be cheap.

---

## 2. Output schema

### `parcel_obs` — one row per (parcel, date). Est. ~20M rows.

```
recinto_id STRING
codigo_municipio STRING
obs_date DATE
season INT
doy INT
item_id STRING              -- provenance; which scene produced this
tile STRING

ndvi_mean DOUBLE            -- from geom_b05 (10 m bands)
ndvi_p50  DOUBLE
ndvi_std  DOUBLE
ndre_mean DOUBLE            -- from geom_b10 (20 m bands)
ndre_p50  DOUBLE
ndre_std  DOUBLE
ndmi_mean DOUBLE
ndmi_p50  DOUBLE

n_px_total INT              -- pixels in the buffered parcel
n_px_valid INT              -- surviving the SCL mask
pct_valid DOUBLE            -- n_px_valid / n_px_total  <- the quality gate
scl_classes MAP<INT,INT>    -- histogram, for diagnosing why pixels were dropped

reliability_class STRING    -- carried from the mask
processed_at TIMESTAMP
```

Wide, not long — three indices per row rather than three rows. Cluster on `(recinto_id, obs_date)`.

`ndvi_std` is the within-parcel variability that `cv_within` was going to provide via H3. **Computed
from pixels directly it is strictly finer than a hex-level CV** — one of the few places dropping H3
improves the product rather than merely simplifying it.

---

## 3. Processing model

**Parallelism unit: one Spark task per `item_id`** (not per band — the six bands of one scene share a
window and must be combined anyway).

Work list: `stac_items` filtered to `tile IN (30TVM, 30TVL, 30TUM, 30TUL)` ⇒ ~1,100 items × 4 seasons.

Per task, using GeoBrix `rst_*` throughout:

1. **Resolve the window.** Union bbox of parcels intersecting this tile, transformed 4258 → 32630,
   intersected with `rst_boundingbox(tile)`. **If the intersection is empty, return zero rows and log
   it — do not read** (§1.4). This guard is mandatory, not defensive.
2. **Open the six bands** with `rst_fromfile(href)`, then `rst_clip` to the window cutline.
   - Pass the cutline as **EWKB** (`ST_AsEWKB`), not plain WKB — WKB is assumed to already be in the
     raster's CRS and is silently *not* reprojected. Our geometry is 4258, the raster is 32630, so
     plain WKB gives a clip in the wrong place with no error. (Spec §17 gotcha 3.)
3. **Decode:** `refl = DN * scale + offset`, masking `nodata` first, via `rst_mapalgebra`. `scale` and
   `offset` come from the `stac_items` row for that (item, band) — never inferred (see NB02 header).
4. **Resample the 20 m bands** to 10 m with `rst_resample_to_res`, **nearest** — we aggregate to
   parcels rather than render, and bilinear would invent values across parcel boundaries.
5. **SCL mask:** keep classes 4 (vegetation) and 5 (bare); drop 3 (shadow), 8/9/10 (cloud), 11 (snow).
   Retain the class histogram via `rst_histogram` so a low `pct_valid` can be explained rather than
   guessed at.
6. **Indices** via `rst_index` using the built-in formulae — `ndvi`, `ndvi_re`, `ndmi` (§0b). No
   hand-written band math, so no chance of a sign or band-order slip.
7. **Zonal stats:** `rst_rasterize_agg` burns the buffered parcel geometries into the window grid with
   `recinto_id` mapped to an integer label; aggregate per label.
   - Rasterise `geom_b05` for the 10 m index and `geom_b10` for the 20 m ones — **two label grids**,
     because the two buffers admit different parcel sets.
8. Return parcel rows to Spark.

**Write per season**, not in one job, so a failure costs one season not four.

**Fallback:** GeoBrix is beta and pinned at 0.4.3. If a specific `rst_*` call blocks, drop to rasterio
+ numpy for that one step only — the `[light]` extra already installs rasterio, so no new dependency
is needed and the surrounding pipeline is unaffected.

---

## 4. QA gates

| # | Gate | Threshold | Action |
|---|---|---|---|
| Q1 | Every read window intersects the raster bounds | 0 empty reads unlogged | **raise** — §1.4 |
| Q2 | Reflectance median in a plausible physical range (red 0.02–0.35) | per scene | **raise** — catches a missing offset |
| Q3 | Water/shadow check: deep-shadow pixels near 0 in NIR after decode | spot check | informational |
| Q4 | `pct_valid` distribution per season | median > 0.6 | investigate — SCL may be over-masking |
| Q5 | Parcels with zero observations in a season | < 2% | investigate — likely tile/footprint gaps |
| Q6 | NDVI ∈ [−1, 1], no NaN in the mean where `n_px_valid > 0` | 0 violations | **raise** |
| Q7 | **Phenology sanity:** DO-mean NDVI by DOY is unimodal, peaks DOY 180–230 | visual + assert peak in window | **the real test that the pipeline works** |

Q7 is the one that matters. If the seasonal curve does not rise in spring, plateau in summer and fall
in autumn, something upstream is wrong and no amount of downstream modelling will fix it.

---

## 5. Order of work

1. Add the buffer columns to NB01, re-run it (~5 min, no re-download needed).
2. Build NB03 against **one item** end to end; verify Q1–Q3 and eyeball a parcel's numbers.
3. Run **one season (2025)**; verify Q4–Q7. This is the go/no-go.
4. Backfill 2022–2024.

---

## 6. Known limits to carry, not fix here

1. **Mixed pixels.** Median parcel is ~13 m wide; most of Ribera has no clean interior pixel, so
   `reliability_class = 'aggregate_only'` covers ~60% of parcels (~10% of area). Per-parcel claims on
   those must be labelled. True vine-only signal needs PNOA row-geometry unmixing (phase 2).
2. **Training system is bimodal** — *vaso* has no rows, *espaldera* does, so the vine-to-soil ratio
   inside a pixel differs fundamentally between parcels.
3. **Boundary covers 82 of 102 municipalities** (2014 MAPA layer, short 13 in Soria). Fix upstream.
4. **SIGPAC licence prohibits commercial exploitation.** Unresolved.

---

## 7. Why H3 is skipped, recorded

Dropping H3 for v0.1 costs:

- **The viz app accelerator.** Spec §9 forks `geospatial-h3-viz-app`, which requires an H3 column. A
  parcel-polygon map is a different build — likely simpler at this scale (69,785 polygons), but it is
  not the same free ride.
- **The multi-source join key.** When InfoRiego weather, the DEM and Sentinel-1 arrive on
  incompatible geometries, H3 is what makes them one `GROUP BY`. Parcel-native means each source needs
  its own resampling to parcels.

And gains:

- **~20M rows instead of ~245M** — an order of magnitude less to store, cluster and query.
- **No re-gridding step.** Sentinel-2 pixels are already a regular aligned grid; H3 res 12 averages
  ~3 pixels per hex into a lattice that matches nothing the sensor produces.
- **Finer within-parcel variance** (§2).

**Re-adoption is cheaper than I first assessed.** GeoBrix ships `rst_h3_rastertogridavg` (and min/max/
median/stddev/sum/count/variance) plus `rst_h3_tessellate` in the light tier — reviving H3 is adding
one call to NB03, not building a bridge table. `parcel_obs` also keeps `recinto_id`, so parcel and hex
outputs can coexist. The decision is genuinely reversible, which is what makes deferring it safe.

If H3 is revived, use **`h3_coverash3`**, not `h3_polyfillash3` — measured, polyfill's centroid-
containment rule drops 10–12% of parcels entirely (thin strips contain no hex centre), while cover
drops 0.0%. Carry `coverage_fraction` to correct cover's edge over-inclusion.
