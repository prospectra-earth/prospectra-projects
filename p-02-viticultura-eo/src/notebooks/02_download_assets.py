# Databricks notebook source
# /// script
# dependencies = [
#   "geobrix[light,stac] @ file:///Volumes/winery_satellite/bronze/raw/wheels/geobrix-0.4.3-py3-none-any.whl",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 02 — Download Assets (windowed COGs → Volume)
# MAGIC
# MAGIC Stages Sentinel-2 band COGs into a Volume, **windowed to the vineyard extent of each tile**.
# MAGIC Recurring job: re-run to pick up new acquisitions. Idempotent — valid files are skipped.
# MAGIC
# MAGIC ### Inputs
# MAGIC
# MAGIC | Source | What |
# MAGIC |---|---|
# MAGIC | `stac_items` | COG hrefs + per-band `scale`/`offset`/`nodata` |
# MAGIC | `ref_vineyard_parcels` | vineyard extent, used to build the per-tile download window |
# MAGIC
# MAGIC ### Output
# MAGIC
# MAGIC | Table | Grain | Purpose |
# MAGIC |---|---|---|
# MAGIC | `s2_assets` | one row per (item, band) | Volume path + validity + the radiometry needed to decode it |
# MAGIC
# MAGIC Files land in `{volume}/s2/{season}/{tile}/{band}_{item_id}.tif`.
# MAGIC
# MAGIC ### Why download rather than read the COGs in place
# MAGIC
# MAGIC GeoBrix's lightweight tier does **not** read remote `https://` COGs directly — verified:
# MAGIC `spark.read.format("gtiff_gbx")` on a remote URL fails to partition, and `rst_fromfile` returns
# MAGIC silent nulls. The supported path, and the one every GeoBrix sample uses, is
# MAGIC `StacClient.download(...)` → Volume → `gtiff_gbx`.
# MAGIC
# MAGIC **"Download" here does not mean whole tiles.** With `bbox` set, `download()` issues rasterio
# MAGIC range reads over `/vsicurl` and transfers **only the AOI pixel window** — the same bytes an
# MAGIC in-place clip would move. The difference is that the window persists as a file, which buys
# MAGIC idempotency (a rerun skips what's already valid, see below) and decouples the slow network
# MAGIC stage from analysis iteration.
# MAGIC
# MAGIC ### Tooling note
# MAGIC
# MAGIC The GeoBrix dependency is declared in the script header above, **not** in `00_setup`, because
# MAGIC `%pip install` of `geobrix[light]` times out on serverless at 80 s (~20 transitive packages).
# MAGIC The header form is declarative, installs at environment build, and works in both the UI and
# MAGIC job runs. Serverless **environment version 5+ is mandatory** — `[light]` needs Python ≥ 3.11.
# MAGIC
# MAGIC Lightweight tier = `pyrx`. The heavyweight `rasterx` tier needs a JAR + GDAL init script on
# MAGIC classic clusters and cannot run on serverless. Both expose identical `rst_*` names.

# COMMAND ----------

# MAGIC %run ./00_setup

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("season", "2025")
# Blank = auto: derive the tile list from stac_items below (NB02's output), filtered to this
# season/bands/cloud threshold. Tiles with no vineyard-parcel intersection (30TWL, 30TWM) are
# dropped a few cells down by the per-tile bbox step, so nothing special is needed to exclude
# them. Set this explicitly only to restrict to a subset — e.g. one tile for a smoke test.
dbutils.widgets.text("tiles", "")
dbutils.widgets.text("bands", "red,nir,rededge1,nir08,swir16,scl")
dbutils.widgets.text("max_cloud_cover", "80")
dbutils.widgets.text("bbox_pad_deg", "0.01")
dbutils.widgets.text("max_items", "0")   # 0 = no cap; set small to smoke-test the download path

SEASON = int(dbutils.widgets.get("season"))
BANDS = [b.strip() for b in dbutils.widgets.get("bands").split(",") if b.strip()]
MAX_CLOUD = float(dbutils.widgets.get("max_cloud_cover"))
PAD = float(dbutils.widgets.get("bbox_pad_deg"))
MAX_ITEMS = int(dbutils.widgets.get("max_items"))

S2_DIR = f"{VOLUME_PATH}/s2/{SEASON}"
T_ASSETS = f"{NS}.s2_assets"

TILES = [t.strip() for t in dbutils.widgets.get("tiles").split(",") if t.strip()]
if not TILES:
    _band_list = ", ".join(f"'{b}'" for b in BANDS)
    TILES = [r.tile for r in spark.sql(f"""
        SELECT DISTINCT tile FROM {T_STAC}
        WHERE season = {SEASON} AND band IN ({_band_list}) AND eo_cloud_cover < {MAX_CLOUD}
        ORDER BY tile
    """).collect()]
    print(f"tiles widget blank -> derived from {T_STAC}: {TILES}")
assert TILES, f"no tiles found in {T_STAC} for season {SEASON} — run 01_ingest_imagery first"

print(f"season {SEASON} | tiles {TILES} | bands {BANDS} | cloud<{MAX_CLOUD}")
print(f"out {S2_DIR}")

# COMMAND ----------

# DBTITLE 1,Per-tile download window
# One bbox per tile: the envelope of vineyard parcels inside that tile's footprint, padded.
# Using the whole-DO bbox for every tile would pull large empty regions for the edge tiles
# (30TUL's vineyard bbox is 79 km2 against the DO's 6,477).

tile_list = ", ".join(f"'{t}'" for t in TILES)

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_tile_bbox AS
    WITH fp AS (
        SELECT tile, ST_Transform(ST_GeomFromGeoJSON(footprint_geojson), {CRS_STORE}) AS geom
        FROM (SELECT tile, footprint_geojson,
                     row_number() OVER (PARTITION BY tile ORDER BY obs_date DESC) rn
              FROM {T_STAC} WHERE band = 'red' AND season = {SEASON} AND tile IN ({tile_list}))
        WHERE rn = 1
    ),
    hit AS (
        SELECT f.tile, p.geometry, p.superficie_m2
        FROM fp f JOIN {T_PARCELS} p ON ST_Intersects(p.geometry, f.geom)
    )
    SELECT tile,
           count(*) AS parcels,
           round(sum(superficie_m2)/10000, 0) AS vineyard_ha,
           ST_XMin(ST_Envelope(ST_Union_Agg(geometry))) - {PAD} AS w,
           ST_YMin(ST_Envelope(ST_Union_Agg(geometry))) - {PAD} AS s,
           ST_XMax(ST_Envelope(ST_Union_Agg(geometry))) + {PAD} AS e,
           ST_YMax(ST_Envelope(ST_Union_Agg(geometry))) + {PAD} AS n
    FROM hit GROUP BY tile
""")

bboxes = {r.tile: (r.w, r.s, r.e, r.n) for r in spark.table("v_tile_bbox").collect()}
assert bboxes, f"no vineyard parcels intersect {TILES} — check the tile names"
missing = [t for t in TILES if t not in bboxes]
if missing:
    print(f"! {missing} contain no vineyard parcels and will be skipped")

display(spark.table("v_tile_bbox"))

# COMMAND ----------

# DBTITLE 1,Download targets + size estimate
# download() requires exactly: item_id, asset_name, href.  Our band column is `band`.
band_list = ", ".join(f"'{b}'" for b in BANDS)

# Created here (not only in the write cell below) so the dedup check right after always has
# a table to query, including on a first run with no prior s2_assets rows at all.
spark.sql(f"""
    CREATE TABLE IF NOT EXISTS {T_ASSETS} (
        season INT, tile STRING, item_id STRING, asset_name STRING, band STRING,
        obs_date DATE, href STRING, out_file_path STRING, out_file_sz BIGINT,
        is_out_file_valid BOOLEAN, scale DOUBLE, offset DOUBLE, nodata INT,
        spatial_resolution INT, eo_cloud_cover DOUBLE, s2_processing_baseline STRING,
        last_update TIMESTAMP
    ) CLUSTER BY (season, tile, band)
""")

# Dedup: skip anything already staged and valid, so a rerun only fetches new acquisitions or
# retries a previously invalid one. This is what makes the "recurring job" claim above true.
spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_targets_all AS
    SELECT s.item_id, s.band AS asset_name, s.href, s.tile, s.obs_date, s.band,
           s.scale, s.offset, s.nodata, s.spatial_resolution, s.eo_cloud_cover, s.s2_processing_baseline
    FROM {T_STAC} s
    WHERE s.season = {SEASON} AND s.tile IN ({tile_list}) AND s.band IN ({band_list})
      AND s.eo_cloud_cover < {MAX_CLOUD}
      AND NOT EXISTS (
          SELECT 1 FROM {T_ASSETS} a
          WHERE a.season = s.season AND a.item_id = s.item_id AND a.asset_name = s.band
            AND a.is_out_file_valid
      )
""")

_candidates = spark.sql(f"""
    SELECT count(*) c FROM {T_STAC}
    WHERE season = {SEASON} AND tile IN ({tile_list}) AND band IN ({band_list})
      AND eo_cloud_cover < {MAX_CLOUD}
""").first().c
_new = spark.table("v_targets_all").count()
_skipped = _candidates - _new
print(f"{_new} assets to fetch" +
      (f" ({_skipped} already downloaded and valid, skipped)" if _skipped else " (none previously downloaded)"))

# Cap by ITEM, never by asset — a partial item is missing a band of an index pair and is
# unusable downstream. Take the clearest items so a smoke test exercises real data.
cap = f"""
    WITH keep AS (
        SELECT item_id FROM (
            SELECT item_id, row_number() OVER (PARTITION BY tile ORDER BY eo_cloud_cover) rn
            FROM (SELECT DISTINCT item_id, tile, eo_cloud_cover FROM v_targets_all))
        WHERE rn <= {MAX_ITEMS})
    SELECT t.* FROM v_targets_all t JOIN keep k ON t.item_id = k.item_id
""" if MAX_ITEMS else "SELECT * FROM v_targets_all"

spark.sql(f"CREATE OR REPLACE TEMP VIEW v_targets AS {cap}")
if MAX_ITEMS:
    print(f"capped to {MAX_ITEMS} item(s) per tile")

est = spark.sql("""
    SELECT t.tile, count(DISTINCT t.item_id) AS items, count(*) AS assets,
           round(b.vineyard_ha) AS vineyard_ha,
           round((b.e-b.w)*111.32*cos(radians((b.s+b.n)/2)) * (b.n-b.s)*110.57, 0) AS bbox_km2,
           round(sum(CASE WHEN t.spatial_resolution = 10 THEN 0.02 ELSE 0.005 END)
                 * ((b.e-b.w)*111.32*cos(radians((b.s+b.n)/2)) * (b.n-b.s)*110.57) / 1024, 1) AS est_gb
    FROM v_targets t JOIN v_tile_bbox b ON t.tile = b.tile
    GROUP BY t.tile, b.vineyard_ha, b.w, b.s, b.e, b.n
    ORDER BY est_gb DESC
""")
display(est)

tot = est.selectExpr("sum(assets) a", "sum(est_gb) g").first()
print(f"\n{tot.a} assets, ~{tot.g:.0f} GB into {S2_DIR}")
print("Estimate assumes uint16 and a full-bbox window; actual is lower where the footprint clips.")

# COMMAND ----------

# DBTITLE 1,Download, one call per tile
# StacClient windows each asset to `bbox` using rasterio range reads over /vsicurl, so only the
# AOI pixels cross the network.  sign=None because Earth Search is public and unsigned
# (the default 'planetary_computer' would try to sign hrefs that need no signing).

from databricks.labs.gbx.stac import StacClient

stac_client = StacClient(catalog=STAC_API_URL, sign=None)
results = []

for tile in [t for t in TILES if t in bboxes]:
    bbox = list(bboxes[tile])
    tgt = spark.table("v_targets").filter(f"tile = '{tile}'")
    n = tgt.count()
    if n == 0:
        print(f"{tile}: nothing new to fetch")
        continue
    out_dir = f"{S2_DIR}/{tile}"
    dbutils.fs.mkdirs(out_dir)
    print(f"{tile}: {n} assets -> {out_dir}")
    print(f"   bbox {[round(v, 4) for v in bbox]}")

    res = stac_client.download(
        tgt.select("item_id", "asset_name", "href"),
        out_dir,
        name="{asset_name}_{item_id}.tif",
        bbox=bbox,
        bbox_crs=f"EPSG:{CRS_STORE}",
        validate=True,          # decode-check each file; drives is_out_file_valid
        max_tries=5,
        partitions=64,          # default is one partition per asset — far too many
    )
    results.append(res.withColumn("tile", lit(tile)))

from functools import reduce
if results:
    dl = reduce(lambda a, b: a.unionByName(b), results)
else:
    # Everything for this run was already staged and valid — an empty, correctly-shaped
    # result so the write/QA cells below run unchanged instead of branching on "nothing to do".
    dl = spark.createDataFrame([], "item_id string, asset_name string, out_file_path string, "
                                    "out_file_sz bigint, is_out_file_valid boolean, "
                                    "last_update timestamp, tile string")
dl.createOrReplaceTempView("v_downloads")
print(f"\n{dl.count()} rows returned")

# COMMAND ----------

# DBTITLE 1,Write s2_assets
# Join the download result back to the catalogue so the manifest carries everything a reader
# needs: the Volume path and the radiometry to decode it. Table itself was already created above,
# so the dedup check has something to query even before this cell has ever run.

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_manifest AS
    SELECT {SEASON} AS season, d.tile, d.item_id, d.asset_name, t.band, t.obs_date, t.href,
           d.out_file_path, d.out_file_sz, d.is_out_file_valid,
           t.scale, t.offset, t.nodata, t.spatial_resolution,
           t.eo_cloud_cover, t.s2_processing_baseline, d.last_update
    FROM v_downloads d
    JOIN v_targets t ON d.item_id = t.item_id AND d.asset_name = t.asset_name
""")

# Idempotent: re-running replaces this season+tile slice rather than appending duplicates.
spark.sql(f"""
    MERGE INTO {T_ASSETS} a
    USING v_manifest m
      ON a.season = m.season AND a.item_id = m.item_id AND a.asset_name = m.asset_name
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
""")
print(f"merged into {T_ASSETS}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## QA

# COMMAND ----------

# DBTITLE 1,Q1 — every asset decoded (raises)
# Invalid rows are not repaired in place: the dedup filter above only skips *valid* rows, so an
# invalid one is picked up and retried automatically the next time this notebook runs.

bad = spark.sql(f"""SELECT count(*) c FROM {T_ASSETS}
                    WHERE season={SEASON} AND NOT is_out_file_valid""").first().c
if bad:
    display(spark.sql(f"""SELECT tile, item_id, asset_name, out_file_sz, href FROM {T_ASSETS}
                          WHERE season={SEASON} AND NOT is_out_file_valid LIMIT 20"""))
    raise AssertionError(f"{bad} assets invalid — rerun this notebook to retry, do not proceed to NB04")
print("Q1 ok — all assets decode")

# COMMAND ----------

# DBTITLE 1,Q2 — completeness and footprint
# Every (item, band) requested should be present. A shortfall means download silently skipped
# assets whose window did not overlap the scene — which is a real condition worth seeing.

display(spark.sql(f"""
    SELECT a.tile, count(DISTINCT a.item_id) AS items, count(*) AS assets,
           count(DISTINCT a.band) AS bands,
           round(sum(a.out_file_sz)/1e9, 2) AS gb,
           round(avg(a.out_file_sz)/1e6, 1) AS avg_mb,
           min(a.obs_date) AS first_obs, max(a.obs_date) AS last_obs
    FROM {T_ASSETS} a WHERE a.season={SEASON} GROUP BY a.tile ORDER BY a.tile
"""))
exp = spark.table("v_targets").count()
got = spark.sql(f"SELECT count(*) c FROM {T_ASSETS} WHERE season={SEASON}").first().c
print(f"{got} of {exp} requested assets present")
if got < exp:
    print("  shortfall — likely assets whose bbox did not overlap the scene footprint")

# COMMAND ----------

# DBTITLE 1,Q3 — all six bands per item
# NB04 computes indices from band pairs, so an item missing one band of a pair is unusable.

display(spark.sql(f"""
    SELECT n_bands, count(*) AS items FROM (
        SELECT item_id, count(DISTINCT band) AS n_bands
        FROM {T_ASSETS} WHERE season={SEASON} AND is_out_file_valid GROUP BY item_id)
    GROUP BY n_bands ORDER BY n_bands
"""))
print(f"expect all items at n_bands = {len(BANDS)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC **NB03 `03_parcel_indices`** — read these with `spark.read.format("gtiff_gbx")`, decode with the
# MAGIC per-asset `scale`/`offset` carried in `s2_assets`, mask on SCL, compute NDVI / NDRE / NDMI via
# MAGIC `rst_index` (built-in formulae `ndvi`, `ndvi_re`, `ndmi`), and zonal-aggregate to parcels with
# MAGIC `rst_rasterize_agg` → `parcel_obs`.
# MAGIC
# MAGIC Coverage is now automatic: leave `tiles` blank and every tile with data in `stac_items` for
# MAGIC the given season/bands/cloud threshold is included, with 30TWL/30TWM dropped downstream (zero
# MAGIC vineyard parcels). Set `tiles` explicitly only to restrict to a subset, e.g. `MGRS-30TVM` alone
# MAGIC for a cheap single-tile smoke test (97.7% of parcels; the other three add ~1,600 more).
# MAGIC
# MAGIC To backfill: re-run with `season` 2022, 2023, 2024. Downloads are idempotent, so an interrupted
# MAGIC run resumes.
