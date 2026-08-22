# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 02 — Ingest Imagery (STAC discovery)
# MAGIC
# MAGIC Catalogues every Sentinel-2 acquisition over the DO. Metadata only — no pixels are read here.
# MAGIC Unlike NB01 this is a **recurring** job: re-run to pick up new acquisitions.
# MAGIC
# MAGIC ### Inputs
# MAGIC
# MAGIC | Source | What |
# MAGIC |---|---|
# MAGIC | `ref_search_envelope` | The search bbox, derived from the DO boundary in NB01 |
# MAGIC | Earth Search STAC API | Sentinel-2 L2A scene metadata, public, no auth |
# MAGIC
# MAGIC ### Output
# MAGIC
# MAGIC | Table | Grain | Purpose |
# MAGIC |---|---|---|
# MAGIC | `stac_items` | one row per **(item, band)** | COG URLs + the scale/offset needed to decode them |
# MAGIC
# MAGIC The (item, band) grain matches the next stage, which parallelises one task per band file.
# MAGIC
# MAGIC ### Two things that would otherwise fail silently
# MAGIC
# MAGIC **The bbox comes from `ref_search_envelope`, never a literal.** The previous hardcoded
# MAGIC `[-4.5, 41.4, -3.2, 41.9]` stopped at longitude −3.2 while the DO reaches −2.894, missing
# MAGIC tiles 30TWL/30TWM — about 20% of the denomination. A search returning fewer results looks
# MAGIC exactly like a region with less imagery.
# MAGIC
# MAGIC **`scale` and `offset` are read per item per band.** Reflectance is `DN * scale + offset`.
# MAGIC ESA reprocessed parts of the archive, so the processing baseline is *not* monotonic in time —
# MAGIC a 2019 scene here carries baseline 05.00 and needs the offset while a 2021 scene carries 03.01
# MAGIC and must not have it. Inferring from date gets 2019 backwards. Forgetting it entirely inflates
# MAGIC NDVI's denominator and can reverse the ranking between parcels, which is exactly what the
# MAGIC vigour product is.
# MAGIC
# MAGIC Collection is **`sentinel-2-l2a`**, not `c1-l2a`: Collection 1 returns zero items over Ribera
# MAGIC for 2022, so switching would silently lose a season.

# COMMAND ----------

# MAGIC %run ./00_setup

# COMMAND ----------

# DBTITLE 1,Parameters
from pystac_client import Client

dbutils.widgets.text("do_id", "ribera_del_duero")
dbutils.widgets.text("start_year", "2022")
dbutils.widgets.text("end_year", "2025")
dbutils.widgets.text("season_start", "04-01")   # DOY 91
dbutils.widgets.text("season_end", "10-31")     # DOY 304 — together, the Winkler window
dbutils.widgets.text("max_cloud_cover", "80")

DO_ID = dbutils.widgets.get("do_id")
SEASONS = list(range(int(dbutils.widgets.get("start_year")), int(dbutils.widgets.get("end_year")) + 1))
SEASON_START = dbutils.widgets.get("season_start")
SEASON_END = dbutils.widgets.get("season_end")
# Loose on purpose (spec D7). Tile-level cloud describes a whole ~110 km MGRS tile while the DO
# occupies a slice of it, so a 70%-cloudy tile can be clear over Ribera. The real filter is
# per-pixel SCL downstream: no scene is rejected, cells are.
MAX_CLOUD = int(dbutils.widgets.get("max_cloud_cover"))

e = spark.table(T_ENVELOPE).where(f"do_id = '{DO_ID}'").first()
assert e is not None, f"no envelope for {DO_ID} — run 01_reference_geometry first"
BBOX = [e.west, e.south, e.east, e.north]

print(f"{DO_ID} | {SEASONS[0]}-{SEASONS[-1]} | {SEASON_START}..{SEASON_END} | cloud < {MAX_CLOUD}%")
print(f"bbox {[round(x, 4) for x in BBOX]}  (from {T_ENVELOPE})")

# COMMAND ----------

# DBTITLE 1,Search
catalog = Client.open(STAC_API_URL)
rows = []
ingested_at = datetime.now(timezone.utc)

for season in SEASONS:
    items = list(catalog.search(
        collections=[S2_COLLECTION],
        bbox=BBOX,
        datetime=f"{season}-{SEASON_START}T00:00:00Z/{season}-{SEASON_END}T23:59:59Z",
        query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
    ).items())
    r = extract_band_rows(items, S2_BANDS, DO_ID, S2_COLLECTION, season, ingested_at)
    rows.extend(r)
    print(f"  {season}: {len(items):4d} items -> {len(r):5d} band rows")

print(f"\ntotal {len(rows)} rows")

# COMMAND ----------

# DBTITLE 1,Write stac_items
pdf = pd.DataFrame(rows)
# scl is categorical and declares neither scale nor offset; coerce so those nulls do not
# poison Spark's type inference for the whole column.
for c in ["scale", "offset", "eo_cloud_cover", "s2_degraded_pct"]:
    pdf[c] = pd.to_numeric(pdf[c], errors="coerce")
for c in ["nodata", "spatial_resolution", "proj_epsg"]:
    pdf[c] = pd.to_numeric(pdf[c], errors="coerce").astype("Int64")

(spark.createDataFrame(pdf).write
     .mode("overwrite").option("overwriteSchema", "true")
     .clusterBy("season", "tile", "band")
     .saveAsTable(T_STAC))

print(f"wrote {spark.table(T_STAC).count()} rows to {T_STAC}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## QA

# COMMAND ----------

# DBTITLE 1,Q1 — every reflectance band declares scale and offset (raises)
missing = spark.sql(f"""
    SELECT season, band, count(*) AS n FROM {T_STAC}
    WHERE band != 'scl' AND (scale IS NULL OR offset IS NULL)
    GROUP BY season, band ORDER BY season, band
""")
if missing.count():
    display(missing)
    raise AssertionError("reflectance bands without scale/offset — do not read pixels from these")
print("Q1 ok")

# COMMAND ----------

# DBTITLE 1,Q2 — baseline is not monotonic in time
# Evidence, not a gate: this is why the offset is read from metadata rather than inferred.

display(spark.sql(f"""
    SELECT season, s2_processing_baseline, offset, count(DISTINCT item_id) AS items
    FROM {T_STAC} WHERE band = 'red'
    GROUP BY season, s2_processing_baseline, offset
    ORDER BY season, s2_processing_baseline
"""))

# COMMAND ----------

# DBTITLE 1,Q3 — acquisition inventory
# Expect ~150-250 items per season. Uneven date counts per tile are correct: tiles at the edge
# are reached by one orbit track rather than two.

display(spark.sql(f"""
    SELECT season, count(DISTINCT item_id) AS items,
           count(DISTINCT obs_date) AS dates, count(DISTINCT tile) AS tiles,
           round(avg(eo_cloud_cover), 1) AS avg_cloud,
           min(obs_date) AS first_obs, max(obs_date) AS last_obs
    FROM {T_STAC} WHERE band = 'red' GROUP BY season ORDER BY season
"""))

display(spark.sql(f"""
    SELECT tile, count(DISTINCT obs_date) AS dates, count(DISTINCT season) AS seasons
    FROM {T_STAC} WHERE band = 'red' GROUP BY tile ORDER BY tile
"""))
print("6 tiles expected: 30TUL, 30TUM, 30TVL, 30TVM, 30TWL, 30TWM")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC **NB04 — read pixels.** Blocked on the vineyard mask: windowing reads to the vineyard extent
# MAGIC is the ~20× byte reduction (~35–50 GB instead of ~500 GB of full tiles). Needs NB02's
# MAGIC `vineyard_mask`, which builds on `ref_vineyard_parcels` from NB01.
