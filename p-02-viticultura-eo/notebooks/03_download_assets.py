# Databricks notebook source
# /// script
# dependencies = [
#   "geobrix[light,stac] @ file:///Volumes/geospatial/ribera_duero/raw/wheels/geobrix-0.4.3-py3-none-any.whl",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 03 — Download Assets (windowed COGs → Volume)
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
# MAGIC idempotency, `validate`/`repair`, and decoupling the slow network stage from analysis iteration.
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
# Tiles that actually contain vineyard. 30TWL/30TWM contain ZERO parcels — never add them.
# 30TVM alone covers 97.7% of parcels; the other three add ~1,600 unique parcels plus extra
# revisits on the overlaps. Start with 30TVM to prove the pipeline cheaply, then widen.
dbutils.widgets.text("tiles", "MGRS-30TVM")
dbutils.widgets.text("bands", "red,nir,rededge1,nir08,swir16,scl")
dbutils.widgets.text("max_cloud_cover", "80")
dbutils.widgets.text("bbox_pad_deg", "0.01")
dbutils.widgets.text("max_items", "0")   # 0 = no cap; set small to smoke-test the download path
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"])

SEASON = int(dbutils.widgets.get("season"))
TILES = [t.strip() for t in dbutils.widgets.get("tiles").split(",") if t.strip()]
BANDS = [b.strip() for b in dbutils.widgets.get("bands").split(",") if b.strip()]
MAX_CLOUD = float(dbutils.widgets.get("max_cloud_cover"))
PAD = float(dbutils.widgets.get("bbox_pad_deg"))
MAX_ITEMS = int(dbutils.widgets.get("max_items"))
DRY_RUN = dbutils.widgets.get("dry_run") == "true"

S2_DIR = f"{VOLUME_PATH}/s2/{SEASON}"
T_ASSETS = f"{NS}.s2_assets"

print(f"season {SEASON} | tiles {TILES} | bands {BANDS} | cloud<{MAX_CLOUD} | dry_run={DRY_RUN}")
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

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_targets_all AS
    SELECT item_id, band AS asset_name, href, tile, obs_date, band,
           scale, offset, nodata, spatial_resolution, eo_cloud_cover, s2_processing_baseline
    FROM {T_STAC}
    WHERE season = {SEASON} AND tile IN ({tile_list}) AND band IN ({band_list})
      AND eo_cloud_cover < {MAX_CLOUD}
""")

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

if DRY_RUN:
    print("\n" + "=" * 74)
    print("DRY RUN — nothing downloaded. Set dry_run=false to fetch.")
    print("=" * 74)

# COMMAND ----------

# DBTITLE 1,Download, one call per tile
# StacClient windows each asset to `bbox` using rasterio range reads over /vsicurl, so only the
# AOI pixels cross the network.  sign=None because Earth Search is public and unsigned
# (the default 'planetary_computer' would try to sign hrefs that need no signing).

if not DRY_RUN:
    from databricks.labs.gbx.stac import StacClient

    stac_client = StacClient(catalog=STAC_API_URL, sign=None)
    results = []

    for tile in [t for t in TILES if t in bboxes]:
        bbox = list(bboxes[tile])
        tgt = spark.table("v_targets").filter(f"tile = '{tile}'")
        n = tgt.count()
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
    dl = reduce(lambda a, b: a.unionByName(b), results)
    dl.createOrReplaceTempView("v_downloads")
    print(f"\n{dl.count()} rows returned")
else:
    print("skipped — dry run")

# COMMAND ----------

# DBTITLE 1,Write s2_assets
# Join the download result back to the catalogue so the manifest carries everything a reader
# needs: the Volume path, the radiometry to decode it, and the href (repair() re-signs from it).

if not DRY_RUN:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {T_ASSETS} (
            season INT, tile STRING, item_id STRING, asset_name STRING, band STRING,
            obs_date DATE, href STRING, out_file_path STRING, out_file_sz BIGINT,
            is_out_file_valid BOOLEAN, scale DOUBLE, offset DOUBLE, nodata INT,
            spatial_resolution INT, eo_cloud_cover DOUBLE, s2_processing_baseline STRING,
            last_update TIMESTAMP
        ) CLUSTER BY (season, tile, band)
    """)

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

# DBTITLE 1,Repair invalid downloads
# validate=True marks any asset rasterio could not decode. repair() re-downloads those rows,
# re-signing the href per attempt. Transient 429/403 from the source is the usual cause.

if not DRY_RUN:
    bad = spark.sql(f"SELECT count(*) c FROM {T_ASSETS} WHERE season={SEASON} AND NOT is_out_file_valid").first().c
    if bad:
        print(f"{bad} invalid — repairing")
        for tile in [t for t in TILES if t in bboxes]:
            n = spark.sql(f"""SELECT count(*) c FROM {T_ASSETS}
                              WHERE season={SEASON} AND tile='{tile}' AND NOT is_out_file_valid""").first().c
            if not n:
                continue
            fixed = stac_client.repair(
                spark.table(T_ASSETS).filter(f"season={SEASON} AND tile='{tile}'"),
                where="is_out_file_valid = false",
                spark=spark,
                out_dir=f"{S2_DIR}/{tile}",
            )
            print(f"   {tile}: {n} repaired -> {fixed.count() if fixed is not None else 0} rows")
    else:
        print("nothing to repair")

# COMMAND ----------

# MAGIC %md
# MAGIC ## QA

# COMMAND ----------

# DBTITLE 1,Q1 — every asset decoded (raises)
if not DRY_RUN:
    bad = spark.sql(f"""SELECT count(*) c FROM {T_ASSETS}
                        WHERE season={SEASON} AND NOT is_out_file_valid""").first().c
    if bad:
        display(spark.sql(f"""SELECT tile, item_id, asset_name, out_file_sz, href FROM {T_ASSETS}
                              WHERE season={SEASON} AND NOT is_out_file_valid LIMIT 20"""))
        raise AssertionError(f"{bad} assets still invalid after repair — do not proceed to NB04")
    print("Q1 ok — all assets decode")

# COMMAND ----------

# DBTITLE 1,Q2 — completeness and footprint
# Every (item, band) requested should be present. A shortfall means download silently skipped
# assets whose window did not overlap the scene — which is a real condition worth seeing.

if not DRY_RUN:
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

if not DRY_RUN:
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
# MAGIC **NB04 `04_parcel_indices`** — read these with `spark.read.format("gtiff_gbx")`, decode with the
# MAGIC per-asset `scale`/`offset` carried in `s2_assets`, mask on SCL, compute NDVI / NDRE / NDMI via
# MAGIC `rst_index` (built-in formulae `ndvi`, `ndvi_re`, `ndmi`), and zonal-aggregate to parcels with
# MAGIC `rst_rasterize_agg` → `parcel_obs`.
# MAGIC
# MAGIC To widen coverage: add `MGRS-30TVL,MGRS-30TUM,MGRS-30TUL` to the `tiles` widget. They add
# MAGIC ~1,600 unique parcels plus extra revisits on the overlaps. Never add 30TWL/30TWM — zero parcels.
# MAGIC
# MAGIC To backfill: re-run with `season` 2022, 2023, 2024. Downloads are idempotent, so an interrupted
# MAGIC run resumes.
