# Databricks notebook source
# /// script
# dependencies = [
#   "geobrix[light] @ file:///Volumes/winery_satellite/bronze/raw/wheels/geobrix-0.4.3-py3-none-any.whl",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 03 — Parcel Indices (NDVI / NDRE / NDMI, parcel-native + H3 res-12)
# MAGIC
# MAGIC Turns the windowed COGs NB03 staged into vineyard signal at two grains:
# MAGIC **parcel-native** (`parcel_obs`, one row per recinto per scene) and **H3 res-12**
# MAGIC (`hex_obs`, one row per hex per scene). Both share the same decoded, masked,
# MAGIC index-computed raster per item — the hex path is one extra call, not a second read.
# MAGIC
# MAGIC ### Inputs
# MAGIC
# MAGIC | Source | What |
# MAGIC |---|---|
# MAGIC | `s2_assets` | NB03's manifest — Volume path + scale/offset/nodata per (item, band) |
# MAGIC | `ref_vineyard_parcels` | `geom_b05`/`geom_b10` pure-pixel buffers + `h3_cells_res13` (NB01) |
# MAGIC | `ref_parcel_h3_xwalk` | VIEW over `ref_vineyard_parcels.h3_cells_res13`, built in NB01 — not built here |
# MAGIC | `stac_items` | per-tile footprint, used once to build the tile→parcel membership |
# MAGIC
# MAGIC ### Outputs
# MAGIC
# MAGIC | Table | Grain | Purpose |
# MAGIC |---|---|---|
# MAGIC | `parcel_obs` | (recinto_id, obs_date) | Consejo-facing grain, wide, with `n_px`/`pct_valid` |
# MAGIC | `hex_obs` | (h3_cell_id, obs_date) | within-parcel resolution a parcel mean erases |
# MAGIC
# MAGIC ### Why the GeoBrix calls below diverge from `plans/PLAN_04_parcel_indices.md`
# MAGIC
# MAGIC That plan assumed `rst_rasterize_agg` computes zonal statistics. **Verified against the
# MAGIC live installed package (inspect.signature + docstring, not docs):** it burns a group's
# MAGIC `(geom, value)` pairs into ONE raster tile — a vector→raster rasterizer, not an aggregator.
# MAGIC There is no built-in zonal-stats function in GeoBrix 0.4.3 light tier. The real mechanism,
# MAGIC used below: `rst_clip` each index raster to one parcel's buffer geometry, then `rst_apply`
# MAGIC — GeoBrix's documented numpy/rasterio escape hatch — to reduce the clipped tile to a
# MAGIC struct of statistics in one call. This is also why the join is (item × parcels-in-tile)
# MAGIC rather than a single rasterize+GROUP BY per item: there is no cheaper verified path.
# MAGIC
# MAGIC Also corrected this build: `rst_h3_rastertogridavg`/`rst_h3_rastertogridcount` are Python
# MAGIC UDTFs (`LATERAL gbx_rst_..(tile, resolution) t`, note the `gbx_` SQL-name prefix) that
# MAGIC assume the input raster is **already EPSG:4326** with no reprojection — an explicit
# MAGIC `rst_transform` precedes both calls. `rst_resample_to_res` defaults to `bilinear`;
# MAGIC `algorithm='nearest'` is passed explicitly (rendering vs. aggregating to parcels/hexes).
# MAGIC (`h3_coverash3` itself now runs in NB01, not here — see `ref_parcel_h3_xwalk` above.)
# MAGIC
# MAGIC **Compute note:** this notebook reads a whole-tile raster window per item (union bbox of
# MAGIC every parcel intersecting that tile) and runs the H3 LATERAL UDTFs over it — both are
# MAGIC memory-heavy per task. It needs a real cluster, not Free Edition serverless (confirmed:
# MAGIC repeated `Python worker exited unexpectedly (crashed)` at `MERGE INTO hex_obs` there,
# MAGIC regardless of spatial chunking, explicit repartitioning, or batching the job into smaller
# MAGIC pieces — none of which changed the outcome, consistent with a genuine capacity ceiling
# MAGIC rather than a tunable parameter).

# COMMAND ----------

# MAGIC %run ./00_setup

# COMMAND ----------

# DBTITLE 1,Imports — GeoBrix pyrx
from databricks.labs.gbx import pyrx as rx
from databricks.labs.gbx.pyrx import functions as prx

rx.assert_rasterio_available()
rx.configure_gdal_env()

# gbx_rst_h3_rastertogridavg/count are SQL UDTFs but are NOT auto-registered on
# import — pyrx.functions.register() must be called explicitly per session
# (verified live: SHOW FUNCTIONS found nothing until this call was made).
prx.register(spark, only=["rst_h3_rastertogridavg", "rst_h3_rastertogridcount"])

from pyspark.sql.types import (
    StructType, StructField, DoubleType, LongType, IntegerType, MapType, StringType
)
from pyspark.sql.functions import array, col as _col, concat, lit as _lit, expr

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("season", "2025")
dbutils.widgets.text("tiles", "")  # blank = every tile with assets staged for this season
dbutils.widgets.text("h3_resolution", "12")

SEASON = int(dbutils.widgets.get("season"))
H3_RES = int(dbutils.widgets.get("h3_resolution"))

T_ASSETS = f"{NS}.s2_assets"
T_PARCEL_OBS = f"{NS_SILVER}.parcel_obs"
T_HEX_OBS = f"{NS_SILVER}.hex_obs"

TILES = [t.strip() for t in dbutils.widgets.get("tiles").split(",") if t.strip()]
if not TILES:
    TILES = [r.tile for r in spark.sql(f"""
        SELECT DISTINCT tile FROM {T_ASSETS} WHERE season = {SEASON} AND is_out_file_valid
        ORDER BY tile
    """).collect()]
assert TILES, f"no valid assets in {T_ASSETS} for season {SEASON} — run 02_download_assets first"
print(f"season {SEASON} | tiles {TILES} | H3 res {H3_RES}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 — tile → parcel membership
# MAGIC
# MAGIC One window per tile (union envelope of every parcel intersecting it), with the buffer
# MAGIC geometries carried along — mirrors NB03's `v_tile_bbox`/`hit` pattern so the per-item join
# MAGIC stays cheap.

# COMMAND ----------

# DBTITLE 1,v_tile_parcels + v_tile_window
tile_list = ", ".join(f"'{t}'" for t in TILES)

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_tile_footprint AS
    SELECT tile, ST_Transform(ST_GeomFromGeoJSON(footprint_geojson), {CRS_METRIC}) AS geom
    FROM (SELECT tile, footprint_geojson,
                 row_number() OVER (PARTITION BY tile ORDER BY obs_date DESC) rn
          FROM {T_STAC} WHERE band = 'red' AND season = {SEASON} AND tile IN ({tile_list}))
    WHERE rn = 1
""")

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_tile_parcels AS
    SELECT f.tile, p.recinto_id, p.codigo_municipio, p.reliability_class,
           p.has_pure_pixel_10m, p.has_pure_pixel_20m,
           ST_Transform(p.geom_b05, {CRS_METRIC}) AS geom_b05_m,
           ST_Transform(p.geom_b10, {CRS_METRIC}) AS geom_b10_m
    FROM v_tile_footprint f
    JOIN {T_PARCELS} p ON ST_Intersects(ST_Transform(p.geometry, {CRS_METRIC}), f.geom)
""")

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_tile_window AS
    SELECT tile, ST_AsEWKB(ST_Envelope(ST_Union_Agg(geom_b05_m))) AS window_ewkb
    FROM v_tile_parcels
    WHERE geom_b05_m IS NOT NULL AND NOT ST_IsEmpty(geom_b05_m)
    GROUP BY tile
""")

display(spark.sql("SELECT tile, count(*) parcels FROM v_tile_parcels GROUP BY tile ORDER BY tile"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 — per-item raster prep: decode, resample, index, guard
# MAGIC
# MAGIC One row per `item_id`. `s2_assets` is pivoted long→wide so every `rst_*` call below is a
# MAGIC vectorized Column expression over the whole item set at once — no per-row Python driver loop.

# COMMAND ----------

# DBTITLE 1,Pivot s2_assets to one row per item
BANDS_10M = ["red", "nir"]
BANDS_20M = ["rededge1", "nir08", "swir16", "scl"]
ALL_BANDS = BANDS_10M + BANDS_20M

_cols = []
for b in ALL_BANDS:
    _cols.append(f"max(CASE WHEN band='{b}' THEN out_file_path END) AS path_{b}")
    _cols.append(f"max(CASE WHEN band='{b}' THEN scale END) AS scale_{b}")
    _cols.append(f"max(CASE WHEN band='{b}' THEN offset END) AS offset_{b}")

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_items AS
    SELECT item_id, tile, obs_date, season, dayofyear(obs_date) AS doy,
           {", ".join(_cols)}
    FROM {T_ASSETS}
    WHERE season = {SEASON} AND tile IN ({tile_list}) AND is_out_file_valid
      AND band IN ({", ".join(f"'{b}'" for b in ALL_BANDS)})
    GROUP BY item_id, tile, obs_date, season
    HAVING {" AND ".join(f"max(CASE WHEN band='{b}' THEN 1 END) = 1" for b in ALL_BANDS)}
""")

items = spark.table("v_items").join(spark.table("v_tile_window"), "tile")
n_items = items.count()
print(f"{n_items} items with all {len(ALL_BANDS)} bands present")
assert n_items > 0, "no complete items — check s2_assets Q3 (all-bands-present) from NB03"

# COMMAND ----------

# DBTITLE 1,Decode + resample every band to a shared 10 m grid
df = items
for b in BANDS_10M:
    raw = prx.rst_clip(prx.rst_fromfile(_col(f"path_{b}")), _col("window_ewkb"), _lit(False))
    dec_expr = concat(_lit("A*"), _col(f"scale_{b}"), _lit("+"), _col(f"offset_{b}"))
    df = df.withColumn(f"tile_{b}", prx.rst_mapalgebra(array(raw), dec_expr))

for b in BANDS_20M:
    raw = prx.rst_clip(prx.rst_fromfile(_col(f"path_{b}")), _col("window_ewkb"), _lit(False))
    if b == "scl":
        # SCL is a class code, not reflectance — resample only, no decode.
        resampled = prx.rst_resample_to_res(raw, _lit(10.0), _lit(10.0), _lit("nearest"))
    else:
        dec_expr = concat(_lit("A*"), _col(f"scale_{b}"), _lit("+"), _col(f"offset_{b}"))
        dec = prx.rst_mapalgebra(array(raw), dec_expr)
        resampled = prx.rst_resample_to_res(dec, _lit(10.0), _lit(10.0), _lit("nearest"))
    df = df.withColumn(f"tile_{b}", resampled)

df.createOrReplaceTempView("v_items_decoded")
print("decoded + resampled: " + ", ".join(f"tile_{b}" for b in ALL_BANDS))

# COMMAND ----------

# DBTITLE 1,Index rasters via rst_derivedband — guard + SCL mask baked in
# One numpy pyfunc, reused for all three indices: in_ar = [low_band, high_band, scl]. Guards the
# measured NDVI blow-up (|denom| < 1e-6 -> NaN, matching PLAN_04 §"measured on the NB03 smoke
# test": p98 = 2.27 on a real scene, SCL alone does not catch it) then clips to [-1, 1], then
# masks to NaN wherever SCL is outside {4 (vegetation), 5 (bare)}.
_INDEX_PYFUNC = """
def stat_scl_masked_index(in_ar, out_ar, xoff, yoff, xsize, ysize, raster_xsize, raster_ysize, buf_radius, gt, **kwargs):
    import numpy as np
    a = in_ar[0].astype('float64')
    b = in_ar[1].astype('float64')
    scl = in_ar[2]
    denom = a + b
    with np.errstate(divide='ignore', invalid='ignore'):
        idx = (b - a) / denom
    idx = np.where(np.abs(denom) < 1e-6, np.nan, idx)
    idx = np.clip(idx, -1.0, 1.0)
    valid_scl = np.isin(scl, [4, 5])
    out_ar[:] = np.where(valid_scl, idx, np.nan)
"""

idx = spark.table("v_items_decoded")
idx = idx.withColumn("stack_ndvi", prx.rst_frombands(array(_col("tile_red"), _col("tile_nir"), _col("tile_scl"))))
idx = idx.withColumn("stack_ndre", prx.rst_frombands(array(_col("tile_rededge1"), _col("tile_nir08"), _col("tile_scl"))))
idx = idx.withColumn("stack_ndmi", prx.rst_frombands(array(_col("tile_nir08"), _col("tile_swir16"), _col("tile_scl"))))

idx = idx.withColumn("ndvi_tile", prx.rst_derivedband(_col("stack_ndvi"), _lit(_INDEX_PYFUNC), _lit("stat_scl_masked_index")))
idx = idx.withColumn("ndre_tile", prx.rst_derivedband(_col("stack_ndre"), _lit(_INDEX_PYFUNC), _lit("stat_scl_masked_index")))
idx = idx.withColumn("ndmi_tile", prx.rst_derivedband(_col("stack_ndmi"), _lit(_INDEX_PYFUNC), _lit("stat_scl_masked_index")))

# idx4: ndvi, ndre, ndmi + raw scl — the scl band stays unmasked so parcel-level stats can
# distinguish "clipped outside the parcel" (scl class 0 / no_data) from "SCL-masked" (present but
# not vegetation/bare) from "valid" (4/5) without re-reading anything.
idx = idx.withColumn("idx4", prx.rst_frombands(array(_col("ndvi_tile"), _col("ndre_tile"), _col("ndmi_tile"), _col("tile_scl"))))
idx.createOrReplaceTempView("v_items_indexed")
print("index rasters built: ndvi_tile, ndre_tile, ndmi_tile, idx4 (4-band)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 — `hex_obs` (H3 res-12, whole window, no per-parcel clip)
# MAGIC
# MAGIC Reuses `ndvi_tile`/`ndre_tile`/`ndmi_tile` from Step 2 unchanged — reprojects once to
# MAGIC EPSG:4326 (required by the light-tier H3 UDTFs, which assume 4326 with no reprojection),
# MAGIC then two LATERAL calls per item: mean and pixel count, joined on `(band, cellID)`.

# COMMAND ----------

# DBTITLE 1,Write hex_obs
spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {T_HEX_OBS} (
        h3_cell_id BIGINT, obs_date DATE, season INT, doy INT, item_id STRING, tile STRING,
        ndvi_mean DOUBLE, ndre_mean DOUBLE, ndmi_mean DOUBLE,
        n_px_ndvi INT, n_px_ndre INT, n_px_ndmi INT,
        processed_at TIMESTAMP
    ) CLUSTER BY (h3_cell_id, obs_date)
""")

# rst_transform/rst_frombands are pyrx Column functions, not SQL-registered routines — build
# via the DataFrame API (matching every other rst_* call in this notebook), not spark.sql().
hex_src = spark.table("v_items_indexed").select(
    "item_id", "tile", "obs_date", "season", "doy",
    prx.rst_transform(
        prx.rst_frombands(array(_col("ndvi_tile"), _col("ndre_tile"), _col("ndmi_tile"))),
        _lit(4326),
    ).alias("idx3_4326"),
)
hex_src.createOrReplaceTempView("v_hex_src")

hex_avg = spark.sql(f"""
    SELECT item_id, tile, obs_date, season, doy, t.band, t.cellID AS h3_cell_id, t.measure AS avg_val
    FROM v_hex_src, LATERAL gbx_rst_h3_rastertogridavg(idx3_4326, {H3_RES}) t
""")
hex_cnt = spark.sql(f"""
    SELECT item_id, t.band, t.cellID AS h3_cell_id, t.measure AS n_px
    FROM v_hex_src, LATERAL gbx_rst_h3_rastertogridcount(idx3_4326, {H3_RES}) t
""")
hex_avg.createOrReplaceTempView("v_hex_avg")
hex_cnt.createOrReplaceTempView("v_hex_cnt")

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_hex_obs AS
    SELECT a.h3_cell_id, a.obs_date, a.season, a.doy, a.item_id, a.tile,
           max(CASE WHEN a.band = 1 THEN a.avg_val END) AS ndvi_mean,
           max(CASE WHEN a.band = 2 THEN a.avg_val END) AS ndre_mean,
           max(CASE WHEN a.band = 3 THEN a.avg_val END) AS ndmi_mean,
           max(CASE WHEN c.band = 1 THEN c.n_px END) AS n_px_ndvi,
           max(CASE WHEN c.band = 2 THEN c.n_px END) AS n_px_ndre,
           max(CASE WHEN c.band = 3 THEN c.n_px END) AS n_px_ndmi
    FROM v_hex_avg a
    LEFT JOIN v_hex_cnt c ON a.item_id = c.item_id AND a.band = c.band AND a.h3_cell_id = c.h3_cell_id
    GROUP BY a.h3_cell_id, a.obs_date, a.season, a.doy, a.item_id, a.tile
""")

spark.sql(f"""
    MERGE INTO {T_HEX_OBS} h
    USING (SELECT *, current_timestamp() AS processed_at FROM v_hex_obs) v
      ON h.h3_cell_id = v.h3_cell_id AND h.item_id = v.item_id
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")

_hex_n = spark.sql(f"SELECT count(*) c FROM {T_HEX_OBS} WHERE season = {SEASON}").first().c
print(f"hex_obs: {_hex_n} rows for season {SEASON}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — `parcel_obs` (pure-pixel zonal stats via `rst_clip` + `rst_apply`)
# MAGIC
# MAGIC Cross-joins each item against the parcels in its tile, clips `idx4` to each parcel's
# MAGIC `geom_b05` (10 m/NDVI) and `geom_b10` (20 m/NDRE+NDMI) separately — the two buffers admit
# MAGIC different parcel sets — then reduces each clip to a stats struct via `rst_apply`.
# MAGIC `n_px_total` counts pixels with any real scene data (`scl != 0`, ESA's own no_data class,
# MAGIC which also catches pixels clipped outside the parcel if the raster's nodata sentinel lands
# MAGIC on 0); `n_px_valid` counts `scl` in {4, 5}.

# COMMAND ----------

# DBTITLE 1,rst_apply stats functions
_STATS_10M_SCHEMA = StructType([
    StructField("ndvi_mean", DoubleType()), StructField("ndvi_p50", DoubleType()),
    StructField("ndvi_std", DoubleType()),
    StructField("n_px_total", LongType()), StructField("n_px_valid", LongType()),
    StructField("scl_classes", MapType(IntegerType(), LongType())),
])
_STATS_20M_SCHEMA = StructType([
    StructField("ndre_mean", DoubleType()), StructField("ndre_p50", DoubleType()),
    StructField("ndre_std", DoubleType()),
    StructField("ndmi_mean", DoubleType()), StructField("ndmi_p50", DoubleType()),
])


def _stats_10m(ds):
    import numpy as np
    arr = ds.read()  # bands: 1=ndvi, 2=ndre, 3=ndmi, 4=scl
    ndvi, scl = arr[0].astype("float64"), arr[3]
    total_mask = scl != 0
    valid_mask = np.isin(scl, [4, 5])
    n_total = int(total_mask.sum())
    n_valid = int(valid_mask.sum())
    vals = ndvi[valid_mask & np.isfinite(ndvi)]
    classes, counts = np.unique(scl[total_mask], return_counts=True)
    return {
        "ndvi_mean": float(np.mean(vals)) if vals.size else None,
        "ndvi_p50": float(np.median(vals)) if vals.size else None,
        "ndvi_std": float(np.std(vals)) if vals.size else None,
        "n_px_total": n_total,
        "n_px_valid": n_valid,
        "scl_classes": {int(c): int(n) for c, n in zip(classes, counts)},
    }


def _stats_20m(ds):
    import numpy as np
    arr = ds.read()  # bands: 1=ndvi, 2=ndre, 3=ndmi, 4=scl
    ndre, ndmi, scl = arr[1].astype("float64"), arr[2].astype("float64"), arr[3]
    valid_mask = np.isin(scl, [4, 5])
    ndre_vals = ndre[valid_mask & np.isfinite(ndre)]
    ndmi_vals = ndmi[valid_mask & np.isfinite(ndmi)]
    return {
        "ndre_mean": float(np.mean(ndre_vals)) if ndre_vals.size else None,
        "ndre_p50": float(np.median(ndre_vals)) if ndre_vals.size else None,
        "ndre_std": float(np.std(ndre_vals)) if ndre_vals.size else None,
        "ndmi_mean": float(np.mean(ndmi_vals)) if ndmi_vals.size else None,
        "ndmi_p50": float(np.median(ndmi_vals)) if ndmi_vals.size else None,
    }

# COMMAND ----------

# DBTITLE 1,Cross-join items x parcels-in-tile, clip, reduce
parcel_src = spark.sql("""
    SELECT i.item_id, i.tile, i.obs_date, i.season, i.doy, i.idx4,
           p.recinto_id, p.codigo_municipio, p.reliability_class,
           p.has_pure_pixel_10m, p.has_pure_pixel_20m,
           ST_AsEWKB(p.geom_b05_m) AS geom_b05_ewkb, ST_AsEWKB(p.geom_b10_m) AS geom_b10_ewkb
    FROM v_items_indexed i
    JOIN v_tile_parcels p ON i.tile = p.tile
""")

parcel_src = parcel_src.withColumn(
    "clip_b05", prx.rst_clip(_col("idx4"), _col("geom_b05_ewkb"), _lit(False))
).withColumn(
    "clip_b10", prx.rst_clip(_col("idx4"), _col("geom_b10_ewkb"), _lit(False))
)

parcel_src = parcel_src.withColumn(
    "stats_10m", prx.rst_apply(_col("clip_b05"), _stats_10m, _STATS_10M_SCHEMA)
).withColumn(
    "stats_20m", prx.rst_apply(_col("clip_b10"), _stats_20m, _STATS_20M_SCHEMA)
)

parcel_obs_df = parcel_src.selectExpr(
    "recinto_id", "codigo_municipio", "obs_date", "season", "doy", "item_id", "tile",
    "reliability_class",
    "stats_10m.ndvi_mean AS ndvi_mean", "stats_10m.ndvi_p50 AS ndvi_p50", "stats_10m.ndvi_std AS ndvi_std",
    "stats_20m.ndre_mean AS ndre_mean", "stats_20m.ndre_p50 AS ndre_p50", "stats_20m.ndre_std AS ndre_std",
    "stats_20m.ndmi_mean AS ndmi_mean", "stats_20m.ndmi_p50 AS ndmi_p50",
    "stats_10m.n_px_total AS n_px_total", "stats_10m.n_px_valid AS n_px_valid",
    "CASE WHEN stats_10m.n_px_total > 0 THEN stats_10m.n_px_valid / stats_10m.n_px_total ELSE NULL END AS pct_valid",
    "stats_10m.scl_classes AS scl_classes",
)
parcel_obs_df.createOrReplaceTempView("v_parcel_obs")

# COMMAND ----------

# DBTITLE 1,Write parcel_obs
spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {T_PARCEL_OBS} (
        recinto_id STRING, codigo_municipio STRING, obs_date DATE, season INT, doy INT,
        item_id STRING, tile STRING, reliability_class STRING,
        ndvi_mean DOUBLE, ndvi_p50 DOUBLE, ndvi_std DOUBLE,
        ndre_mean DOUBLE, ndre_p50 DOUBLE, ndre_std DOUBLE,
        ndmi_mean DOUBLE, ndmi_p50 DOUBLE,
        n_px_total INT, n_px_valid INT, pct_valid DOUBLE,
        scl_classes MAP<INT, BIGINT>,
        processed_at TIMESTAMP
    ) CLUSTER BY (recinto_id, obs_date)
""")

spark.sql(f"""
    MERGE INTO {T_PARCEL_OBS} t
    USING (SELECT *, current_timestamp() AS processed_at FROM v_parcel_obs) v
      ON t.recinto_id = v.recinto_id AND t.item_id = v.item_id
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")

_parcel_n = spark.sql(f"SELECT count(*) c FROM {T_PARCEL_OBS} WHERE season = {SEASON}").first().c
print(f"parcel_obs: {_parcel_n} rows for season {SEASON}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## QA

# COMMAND ----------

# DBTITLE 1,Q1 — no silently-empty clips (raises)
bad = spark.sql(f"""
    SELECT count(*) c FROM {T_PARCEL_OBS}
    WHERE season = {SEASON} AND reliability_class = 'parcel' AND n_px_total = 0
""").first().c
if bad:
    raise AssertionError(f"{bad} 'parcel'-class rows got an empty clip — window/CRS bug, not a data gap")
print("Q1 ok — every reliability_class='parcel' row saw at least one pixel")

# COMMAND ----------

# DBTITLE 1,Q2 — reflectance-derived index sanity, Q6 range (raises)
bad_range = spark.sql(f"""
    SELECT count(*) c FROM {T_PARCEL_OBS}
    WHERE season = {SEASON} AND (ndvi_mean < -1 OR ndvi_mean > 1
                                  OR ndre_mean < -1 OR ndre_mean > 1
                                  OR ndmi_mean < -1 OR ndmi_mean > 1)
""").first().c
if bad_range:
    raise AssertionError(f"{bad_range} rows outside [-1,1] — clip guard in _INDEX_PYFUNC did not hold")
print("Q6 ok — all indices in [-1, 1]")

display(spark.sql(f"""
    SELECT round(avg(ndvi_mean),3) ndvi, round(avg(ndre_mean),3) ndre, round(avg(ndmi_mean),3) ndmi,
           round(median(pct_valid),3) median_pct_valid
    FROM {T_PARCEL_OBS} WHERE season = {SEASON}
"""))

# COMMAND ----------

# DBTITLE 1,Q4/Q5 — validity and coverage
display(spark.sql(f"""
    SELECT reliability_class, count(*) rows, round(median(pct_valid),3) median_pct_valid,
           round(100.0 * sum(CASE WHEN n_px_valid = 0 THEN 1 ELSE 0 END) / count(*), 1) AS pct_zero_obs
    FROM {T_PARCEL_OBS} WHERE season = {SEASON} GROUP BY reliability_class
"""))
print("Q4 target: median_pct_valid > 0.6 for reliability_class='parcel'")
print("Q5 target: pct_zero_obs < 2% for reliability_class='parcel'")

# COMMAND ----------

# DBTITLE 1,Q7 — phenology sanity (the real end-to-end test)
display(spark.sql(f"""
    SELECT doy, round(avg(ndvi_mean),3) AS ndvi
    FROM {T_PARCEL_OBS} WHERE season = {SEASON} AND reliability_class = 'parcel'
    GROUP BY doy ORDER BY doy
"""))
print("Expect a unimodal curve peaking DOY 180-230. On a 6-item smoke test this is a shape check, "
      "not yet the assert-worthy signal a full season gives.")

# COMMAND ----------

# DBTITLE 1,Q8 — cross-check parcel_obs vs hex_obs (informational)
# ref_parcel_h3_xwalk is res-13 (NB01); hex_obs is res-12 — roll up via h3_toparent at query
# time (free, pure index arithmetic) rather than precomputing a res-12 crosswalk. DISTINCT
# before the join so a parcel with several res-13 children under the same res-12 parent
# doesn't get that parent hex double-counted.
display(spark.sql(f"""
    WITH parcel_hex12 AS (
        SELECT DISTINCT recinto_id, h3_toparent(h3_cell_id, {H3_RES}) AS h3_cell_id
        FROM {NS}.ref_parcel_h3_xwalk
    ),
    hex_rollup AS (
        SELECT x.recinto_id, h.item_id,
               avg(h.ndvi_mean) AS ndvi_from_hex,
               count(*) AS n_hexes
        FROM {T_HEX_OBS} h JOIN parcel_hex12 x ON h.h3_cell_id = x.h3_cell_id
        WHERE h.season = {SEASON}
        GROUP BY x.recinto_id, h.item_id HAVING count(*) >= 3
    )
    SELECT round(avg(abs(p.ndvi_mean - r.ndvi_from_hex)), 4) AS mean_abs_diff, count(*) AS n_compared
    FROM {T_PARCEL_OBS} p JOIN hex_rollup r ON p.recinto_id = r.recinto_id AND p.item_id = r.item_id
    WHERE p.season = {SEASON}
"""))
print("Informational only — geom_b05 (pure-pixel) vs full-footprint coverage means some divergence is expected.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC **Go/no-go gate** (per `plans/PLAN_04_parcel_indices.md` §5 and the build plan): re-run
# MAGIC `02_download_assets` for the full 2025 season (`max_items=0`, `tiles` blank), then re-run
# MAGIC this notebook over that season. Verify Q4–Q7 on `parcel_obs` — Q7's unimodal curve across a
# MAGIC real season is the test that actually proves the pipeline works — and spot-check `hex_obs`
# MAGIC row count before committing to the 2022–2024 backfill at hex grain.
# MAGIC
# MAGIC Q8's rollup is an unweighted average across a parcel's res-12 parent hexes (no
# MAGIC `coverage_fraction` — the res-13 assignment in NB01 is a strict membership array, not a
# MAGIC fractional overlap), so some divergence from `parcel_obs` is structurally expected, not a bug.
