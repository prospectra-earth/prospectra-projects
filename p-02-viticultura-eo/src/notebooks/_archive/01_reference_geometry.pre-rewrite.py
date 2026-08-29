# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# DBTITLE 1,NB01 — Reference Geometry
# MAGIC %md
# MAGIC # 01 — Vineyard Reference Geometry
# MAGIC
# MAGIC **Project:** `viticultura-eo` — Vineyard parcel reference data
# MAGIC
# MAGIC ## What this notebook does
# MAGIC
# MAGIC Loads vineyard parcel geometries from an uploaded shapefile and enriches them with municipality data.
# MAGIC
# MAGIC **Workflow:**
# MAGIC 1. Load vineyard shapefile (`.zip`) from Unity Catalog volume
# MAGIC 2. Validate CRS and geometry
# MAGIC 3. Join with municipios table to add municipality metadata
# MAGIC 4. Calculate parcel areas
# MAGIC 5. Write to `ref_vineyard_parcels` Delta table
# MAGIC
# MAGIC ## Usage
# MAGIC
# MAGIC This is an **on-demand notebook** — run it whenever you have new vineyard data.
# MAGIC
# MAGIC **Steps:**
# MAGIC 1. Upload your vineyard shapefile (`.zip`) to `/Volumes/{catalog}/{schema}/raw/`
# MAGIC 2. Set the `shapefile_name` parameter
# MAGIC 3. Run all cells
# MAGIC
# MAGIC **Parameters:**
# MAGIC * `catalog`, `schema` — Unity Catalog location
# MAGIC * `shapefile_name` — Name of uploaded `.zip` file (e.g., `calidaddiferenciada_vinos.zip`)
# MAGIC * `region_id` — Region identifier (e.g., `ribera_del_duero`)
# MAGIC
# MAGIC **Output table:** `{catalog}.{schema}.ref_vineyard_parcels`
# MAGIC
# MAGIC ## Design decisions
# MAGIC
# MAGIC | Decision | Rationale |
# MAGIC |---|---|
# MAGIC | **CRS discipline** | EPSG:4258 (ETRS89) for storage; EPSG:25830 (UTM 30N) for area calculations |
# MAGIC | **Generic shapefile input** | Accept any vineyard shapefile — region-agnostic |
# MAGIC | **Municipality join** | Spatial join adds municipality context for queries |

# COMMAND ----------

# DBTITLE 1,Import utilities
# MAGIC %run ./00_setup

# COMMAND ----------

# DBTITLE 1,Install geopandas for spatial operations
# MAGIC %pip install geopandas pyogrio requests --quiet

# COMMAND ----------

# DBTITLE 1,Create parameter widgets
# Create parameter widgets
dbutils.widgets.text("catalog", "geospatial")
dbutils.widgets.text("schema", "ribera_duero")
dbutils.widgets.text("shapefile_name", "calidaddiferenciada_vinos.zip")
dbutils.widgets.text("region_id", "ribera_del_duero")

# COMMAND ----------

# DBTITLE 1,Configuration
# Parse parameters from widgets
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
SHAPEFILE_NAME = dbutils.widgets.get("shapefile_name")
REGION_ID = dbutils.widgets.get("region_id")

# Paths
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/raw"
SHAPEFILE_PATH = f"{VOLUME_PATH}/{SHAPEFILE_NAME}"

# Output table
TABLE_VINEYARD_PARCELS = f"{CATALOG}.{SCHEMA}.ref_vineyard_parcels"

# Source municipios table (existing)
SOURCE_MUNICIPIOS = "geospatial.spain_population_analysis.municipios_geo_raw"

print(f"Region:            {REGION_ID}")
print(f"Shapefile:         {SHAPEFILE_PATH}")
print(f"Output table:      {TABLE_VINEYARD_PARCELS}")
print(f"Municipios source: {SOURCE_MUNICIPIOS}")

# COMMAND ----------

# DBTITLE 1,Bootstrap Unity Catalog
spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

print(f"Catalog `{CATALOG}.{SCHEMA}` ready")

# COMMAND ----------

# DBTITLE 1,Download SIGPAC parcels
# MAGIC %md
# MAGIC ## Step 1: Load DO boundary shapefile
# MAGIC
# MAGIC The shapefile containing the DO boundary should be uploaded to the Unity Catalog volume at:
# MAGIC `/Volumes/{catalog}/{schema}/raw/{shapefile_name}`
# MAGIC
# MAGIC For **Ribera del Duero**, this is typically the MAPA (Ministerio de Agricultura) shapefile:
# MAGIC * **Source:** All Spanish wine DOP/IGP zones, single shapefile
# MAGIC * **Vintage:** 2014-03 (March 2014)
# MAGIC * **Scale:** 1:25,000, ETRS89
# MAGIC
# MAGIC For **other regions**, upload the appropriate boundary shapefile and update the `shapefile_name` parameter.

# COMMAND ----------

# DBTITLE 1,Fetch SIGPAC vineyard parcels via WFS
from pathlib import Path

# Ensure volume exists
try:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.raw")
    print(f"Volume ready: {VOLUME_PATH}")
except Exception as e:
    print(f"Volume creation failed (may already exist): {e}")

# Verify shapefile exists
zip_path = Path(SHAPEFILE_PATH)

if not zip_path.exists():
    raise FileNotFoundError(
        f"Shapefile not found: {zip_path}\n"
        f"Please upload your shapefile to the volume at {VOLUME_PATH}/"
    )

print(f"Shapefile found: {zip_path}")
print(f"File size: {zip_path.stat().st_size / 1024 / 1024:.2f} MB")

# COMMAND ----------

# DBTITLE 1,Apply buffer and area filter
# Extract and read shapefile from zip
import zipfile
import tempfile
import geopandas as gpd

print(f"Extracting shapefile from {zip_path}...")

# Extract to temporary directory
with tempfile.TemporaryDirectory() as tmpdir:
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(tmpdir)
    
    # Find the .shp file
    shp_files = list(Path(tmpdir).rglob("*.shp"))
    
    if not shp_files:
        raise FileNotFoundError(f"No .shp file found in {zip_path}")
    
    print(f"Found {len(shp_files)} shapefile(s): {[f.name for f in shp_files]}")
    
    # Read the first shapefile
    print(f"Reading {shp_files[0].name}...")
    gdf_all = gpd.read_file(shp_files[0])

print(f"\nLoaded {len(gdf_all)} features")
print(f"CRS: {gdf_all.crs}")
print(f"\nColumns: {gdf_all.columns.tolist()}")
print(f"\nFirst 3 rows (name column sample):")
display(gdf_all.head(3))

# COMMAND ----------

# DBTITLE 1,Write to Delta
# Filter to Ribera del Duero
# Match case-insensitively and accent-tolerantly
# The attribute schema is unverified — inspect the output above first
# Common patterns: 'RIBERA DEL DUERO', 'D.O.P. Ribera del Duero', etc.

# TODO: Adjust the column name and matching logic based on the schema inspection above
# This is a template — replace 'NAME_COLUMN' with the actual column name

def normalize_text(s):
    """Normalize for case-insensitive, accent-tolerant matching"""
    import unicodedata
    s = s.lower()
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
    return s

# PLACEHOLDER: Replace 'nombre' with the actual name column from the schema above
name_col = 'nombre'  # Adjust based on schema inspection

if name_col not in gdf_all.columns:
    print(f"\n⚠️  Column '{name_col}' not found. Available columns:")
    print(gdf_all.columns.tolist())
    print("\nUpdate the name_col variable above based on the actual schema.")
else:
    gdf_all['normalized_name'] = gdf_all[name_col].astype(str).apply(normalize_text)
    
    # Filter to Ribera del Duero
    mask = gdf_all['normalized_name'].str.contains('ribera.*duero', regex=True)
    gdf_ribera = gdf_all[mask].copy()
    
    print(f"\nFiltered to {len(gdf_ribera)} features matching 'ribera.*duero'")
    
    if len(gdf_ribera) == 0:
        print("⚠️  No features found. Check the name column and filter logic.")
        print("\nUnique names in dataset (first 20):")
        print(gdf_all[name_col].unique()[:20])
    else:
        # Dissolve to single geometry if multiple features
        if len(gdf_ribera) > 1:
            print(f"Dissolving {len(gdf_ribera)} features to single geometry...")
            gdf_ribera = gdf_ribera.dissolve().reset_index(drop=True)
        
        # Reproject to EPSG:4258 if needed
        if gdf_ribera.crs.to_epsg() != 4258:
            print(f"Reprojecting from {gdf_ribera.crs} to EPSG:4258...")
            gdf_ribera = gdf_ribera.to_crs("EPSG:4258")
        
        # Calculate area in km² (transform to metric CRS first)
        geom_metric = gdf_ribera.to_crs("EPSG:25830").geometry.iloc[0]
        area_km2 = geom_metric.area / 1_000_000
        
        print(f"\nDO boundary prepared:")
        print(f"  Features: {len(gdf_ribera)}")
        print(f"  CRS: {gdf_ribera.crs}")
        print(f"  Area: {area_km2:.1f} km²")
        
        # QA gate: area should be between 5,000 and 20,000 km²
        if not (5_000 <= area_km2 <= 20_000):
            print(f"\n⚠️  WARNING: Area {area_km2:.1f} km² outside expected range [5k–20k km²]")
            print("   Wrong feature may have been selected. Stop and investigate.")
        else:
            print(f"\n✓ Area within expected range")

# COMMAND ----------

# DBTITLE 1,Write DO boundary to Delta
import pandas as pd
from datetime import datetime

# Prepare data for Delta
if 'gdf_ribera' in locals() and len(gdf_ribera) == 1:
    # Convert geometry to WKT
    geom_wkt = gdf_ribera.geometry.iloc[0].wkt
    
    # Create DataFrame with metadata
    data = {
        'do_id': [DO_ID],
        'do_name': [gdf_ribera[name_col].iloc[0]],
        'geometry_wkt': [geom_wkt],
        'area_km2': [area_km2],
        'source': [MAPA_URL],
        'source_vintage': [MAPA_VINTAGE],
        'extracted_at': [datetime.now()]
    }
    
    pdf = pd.DataFrame(data)
    df = spark.createDataFrame(pdf)
    
    # Write to Delta
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLE_DO_BOUNDARY)
    
    print(f"\n✓ Wrote DO boundary to {TABLE_DO_BOUNDARY}")
    
    # Convert WKT to geometry for storage
    spark.sql(f"""
        CREATE OR REPLACE TABLE {TABLE_DO_BOUNDARY} AS
        SELECT 
            do_id,
            do_name,
            ST_GeomFromText(geometry_wkt, 4258) AS geometry,
            area_km2,
            source,
            source_vintage,
            extracted_at
        FROM {TABLE_DO_BOUNDARY}
    """)
    
    print("Converted WKT to native geometry column")
    display(spark.table(TABLE_DO_BOUNDARY))
else:
    print("\n⚠️  Skip write — gdf_ribera not ready or empty")

# COMMAND ----------

# DBTITLE 1,QA gates
# MAGIC %md
# MAGIC ## Step 2: Derive municipality reference table
# MAGIC
# MAGIC Spatial intersect with IGN municipal polygons, computing overlap fraction per municipality.

# COMMAND ----------

# DBTITLE 1,QA 1 — Total vineyard area sanity check
# Spatial intersect with municipal polygons
# Computing overlap fraction to identify partial municipalities

spark.sql(f"""
    CREATE OR REPLACE TABLE {TABLE_MUNICIPIOS} AS
    WITH do_poly AS (
        SELECT do_id, geometry AS do_geom
        FROM {TABLE_DO_BOUNDARY}
    ),
    isect AS (
        SELECT
            d.do_id,
            m.codigo_municipio,
            m.text AS municipio,
            m.geometry,
            ST_Area(ST_Transform(m.geometry, 25830)) / 10000.0 AS muni_ha,
            ST_Area(ST_Transform(ST_Intersection(m.geometry, d.do_geom), 25830)) / 10000.0 AS inside_ha
        FROM {SOURCE_MUNICIPIOS} m
        JOIN do_poly d
            ON ST_Intersects(m.geometry, d.do_geom)
    )
    SELECT
        do_id,
        codigo_municipio,
        municipio,
        geometry,
        muni_ha,
        inside_ha,
        inside_ha / muni_ha AS frac_inside,
        (inside_ha / muni_ha) < 0.99 AS is_partial,
        '{SOURCE_MUNICIPIOS}' AS source_table,
        current_timestamp() AS extracted_at
    FROM isect
    WHERE inside_ha / muni_ha >= {MIN_FRAC_INSIDE}
""")

print(f"\n✓ Created {TABLE_MUNICIPIOS}")

# Display summary
df_muni = spark.table(TABLE_MUNICIPIOS)
n_municipios = df_muni.count()
print(f"\nMunicipalities intersecting DO: {n_municipios}")
print(f"Expected: ~115 (as of 2014 MAPA vintage)")

if n_municipios < 100:
    print(f"\n⚠️  WARNING: Only {n_municipios} municipalities found")
    print("   Expected ~115. The 2014 MAPA layer may be stale.")

display(df_muni.limit(10))

# COMMAND ----------

# DBTITLE 1,QA 2 — Geometry validity
# Distribution of frac_inside to identify partial municipalities
print("\nDistribution of frac_inside:")
display(spark.sql(f"""
    SELECT 
        CASE 
            WHEN frac_inside < 0.25 THEN '< 25%'
            WHEN frac_inside < 0.50 THEN '25-50%'
            WHEN frac_inside < 0.75 THEN '50-75%'
            WHEN frac_inside < 0.99 THEN '75-99%'
            ELSE '≥99% (complete)'
        END AS frac_bin,
        count(*) AS n_municipios
    FROM {TABLE_MUNICIPIOS}
    GROUP BY frac_bin
    ORDER BY 
        CASE frac_bin
            WHEN '< 25%' THEN 1
            WHEN '25-50%' THEN 2
            WHEN '50-75%' THEN 3
            WHEN '75-99%' THEN 4
            ELSE 5
        END
"""))

# List partial municipalities explicitly
print("\nPartial municipalities (< 99% inside DO):")
display(spark.sql(f"""
    SELECT municipio, round(frac_inside * 100, 1) AS pct_inside
    FROM {TABLE_MUNICIPIOS}
    WHERE is_partial = true
    ORDER BY frac_inside
"""))

# COMMAND ----------

# DBTITLE 1,QA 3 — Area distribution
# Validate no frac_inside > 1.0 (would indicate geometry or CRS error)
max_frac = spark.sql(f"""
    SELECT max(frac_inside) AS max_frac
    FROM {TABLE_MUNICIPIOS}
""").collect()[0].max_frac

print(f"\nMax frac_inside: {max_frac:.4f}")

if max_frac > 1.0:
    print(f"\n⚠️  ERROR: frac_inside > 1.0 detected (max={max_frac:.4f})")
    print("   This indicates a geometry or CRS error. Stop and investigate.")
else:
    print("\n✓ QA PASS — All frac_inside ≤ 1.0")

# COMMAND ----------

# DBTITLE 1,Step 3: Derive STAC search envelope
# MAGIC %md
# MAGIC ## Step 3: Derive STAC search envelope
# MAGIC
# MAGIC Buffer the DO boundary by 2 km (in metric CRS) and envelope to create the bounding box for satellite imagery searches.
# MAGIC
# MAGIC This replaces the hardcoded bbox currently in `01_ingest_imagery`.

# COMMAND ----------

# DBTITLE 1,Create search envelope
# Create search envelope with 2 km buffer
spark.sql(f"""
    CREATE OR REPLACE TABLE {TABLE_ENVELOPE} AS
    SELECT
        do_id,
        ST_Transform(
            ST_Envelope(ST_Buffer(ST_Transform(geometry, 25830), {SEARCH_BUFFER_M})),
            4258
        ) AS envelope,
        {SEARCH_BUFFER_M} AS buffer_m,
        current_timestamp() AS extracted_at
    FROM {TABLE_DO_BOUNDARY}
""")

print(f"\n✓ Created {TABLE_ENVELOPE}")
display(spark.table(TABLE_ENVELOPE))

# COMMAND ----------

# DBTITLE 1,Extract bbox ordinates
# Extract west, south, east, north ordinates for easy consumption by 01_ingest_imagery
spark.sql(f"""
    CREATE OR REPLACE TABLE {TABLE_ENVELOPE} AS
    SELECT
        do_id,
        envelope,
        buffer_m,
        ST_XMin(envelope) AS west,
        ST_YMin(envelope) AS south,
        ST_XMax(envelope) AS east,
        ST_YMax(envelope) AS north,
        extracted_at
    FROM {TABLE_ENVELOPE}
""")

print("\nAdded bbox ordinates:")
df_env = spark.table(TABLE_ENVELOPE)
display(df_env)

# COMMAND ----------

# DBTITLE 1,QA: Validate search envelope
# QA gates for search envelope

# 1. Envelope must contain the DO boundary
contains = spark.sql(f"""
    SELECT 
        ST_Contains(e.envelope, b.geometry) AS contains_do
    FROM {TABLE_ENVELOPE} e
    JOIN {TABLE_DO_BOUNDARY} b
        ON e.do_id = b.do_id
""").collect()[0].contains_do

if not contains:
    print("\n⚠️  ERROR: Envelope does not contain DO boundary")
else:
    print("\n✓ QA PASS — Envelope contains DO boundary")

# 2. Sanity-check dimensions in metric CRS
row = df_env.collect()[0]
west, south, east, north = row.west, row.south, row.east, row.north

# Transform corners to metric CRS for dimension check
from pyspark.sql.functions import expr

width_km = spark.sql(f"""
    SELECT 
        ST_Distance(
            ST_Transform(ST_Point({west}, {south}, 4258), 25830),
            ST_Transform(ST_Point({east}, {south}, 4258), 25830)
        ) / 1000.0 AS width_km
""").collect()[0].width_km

height_km = spark.sql(f"""
    SELECT 
        ST_Distance(
            ST_Transform(ST_Point({west}, {south}, 4258), 25830),
            ST_Transform(ST_Point({west}, {north}, 4258), 25830)
        ) / 1000.0 AS height_km
""").collect()[0].height_km

print(f"\nEnvelope dimensions:")
print(f"  Width:  {width_km:.1f} km")
print(f"  Height: {height_km:.1f} km")
print(f"  Bbox:   [{west:.3f}, {south:.3f}, {east:.3f}, {north:.3f}]")

# Sanity check: roughly 100-130 km × 50-70 km
if not (80 <= width_km <= 150 and 40 <= height_km <= 90):
    print(f"\n⚠️  WARNING: Dimensions outside expected range")
    print("   Expected: 100-130 km × 50-70 km (approximate)")
else:
    print("\n✓ QA PASS — Dimensions within expected range")

# COMMAND ----------

# DBTITLE 1,Next
# MAGIC %md
# MAGIC ## Step 4: Rewire `01_ingest_imagery`
# MAGIC
# MAGIC Update `01_ingest_imagery` to read its bbox from `ref_search_envelope` instead of the hardcoded coordinates.
# MAGIC
# MAGIC ### Changes needed:
# MAGIC
# MAGIC 1. **Delete hardcoded bbox** — Remove the magic number `[-4.5, 41.4, -3.2, 41.9]`
# MAGIC
# MAGIC 2. **Read from table** — Add a cell after configuration that reads the envelope:
# MAGIC    ```python
# MAGIC    bbox_row = spark.sql(f"SELECT west, south, east, north FROM {CATALOG}.{SCHEMA}.ref_search_envelope").collect()[0]
# MAGIC    bbox = [bbox_row.west, bbox_row.south, bbox_row.east, bbox_row.north]
# MAGIC    print(f"Search bbox: {bbox}")
# MAGIC    ```
# MAGIC
# MAGIC 3. **Update catalog/schema** — Change widget defaults from `viticultura`/`bronze` to `geospatial`/`ribera_duero` to match where `stac_items` actually lives.
# MAGIC
# MAGIC ## Next
# MAGIC
# MAGIC Once Part A is complete and `01_ingest_imagery` is rewired:
# MAGIC
# MAGIC **Part B — SIGPAC vineyard mask:**
# MAGIC * Download SIGPAC parcels for the municipalities in `ref_do_municipios`
# MAGIC * Filter to `uso = 'VI'` (vineyard land use)
# MAGIC * Apply −15 m buffer, drop parcels < 0.5 ha
# MAGIC * Persist as `geospatial.ribera_duero.vineyard_mask`
# MAGIC
# MAGIC **Then NB04 `04_read_pixels`:**
# MAGIC * Windowed raster reads using the vineyard mask extent
# MAGIC * Apply scale/offset to convert DN → reflectance
# MAGIC * Persist as `pixels.reflectance`
# MAGIC * ~20× byte reduction: windowed reads (~35–50 GB) vs. full-tile reads (~500 GB)

# COMMAND ----------

