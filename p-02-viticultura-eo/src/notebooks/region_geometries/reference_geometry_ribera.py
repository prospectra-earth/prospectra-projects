# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Reference Geometry — Ribera del Duero
# MAGIC
# MAGIC Where the DO is, which municipalities it covers, and where every vineyard parcel sits.
# MAGIC **Manually triggered, run once per DO** — cadastral geometry is near-static (new plantings
# MAGIC authorised in Ribera for 2026: 0.1 ha). Re-run only for a new SIGPAC vintage, a boundary
# MAGIC revision, or a new denomination.
# MAGIC
# MAGIC ### Inputs
# MAGIC
# MAGIC | Source | What | Where |
# MAGIC |---|---|---|
# MAGIC | MAPA appellation layer | 1 polygon — the DO's legal boundary. No parcel data. | `{volume}/calidaddiferenciada_vinos.zip` (manual download) |
# MAGIC | SIGPAC `RECFE` (ITACyL) | ~3,000 land parcels per municipality, with land-use codes | `{volume}/sigpac/{year}/*.zip` (manual download) |
# MAGIC | `municipios_geo_raw` | Spanish municipal polygons, native `geometry(4258)` | table, from P-01 |
# MAGIC
# MAGIC ### Outputs
# MAGIC
# MAGIC | Table | Rows | Purpose |
# MAGIC |---|---|---|
# MAGIC | `ref_do_boundary` | 1 | DO polygon |
# MAGIC | `ref_municipios` | ~82 | Municipality list + INE↔cadastral crosswalk + download URLs |
# MAGIC | `ref_search_envelope` | 1 | STAC bbox — **the only source of truth for it** |
# MAGIC | `ref_vineyard_parcels` | ~70k | `uso='VI'` parcels clipped to the DO — **the deliverable** |
# MAGIC | `ref_uso_audit` | small | Area by land-use code; evidence for the VI-only decision |
# MAGIC
# MAGIC `ref_parcel_h3_xwalk` is a **view**, not a table — see the note above the parcel write cell.
# MAGIC
# MAGIC Everything else is a temp view.
# MAGIC
# MAGIC ### Two phases, manual download in between
# MAGIC
# MAGIC You cannot know which SIGPAC zips to fetch until the municipality list exists. Phase 1 needs
# MAGIC only the boundary zip and emits the download list; Phase 2 reads the parcels. Running before
# MAGIC downloading completes Phase 1 and stops cleanly.
# MAGIC
# MAGIC ### Gotchas, all verified against the real files
# MAGIC
# MAGIC - DO boundary key is **`ZON_CO_ID = 49`**; the name column is `ZON_DS_NOM` (`nombre` does not exist).
# MAGIC - SIGPAC ships in **degrees (4258)** despite the metadata saying UTM, so `Shape_Area` is unusable. `SUPERFICIE` is genuine m².
# MAGIC - **`substr(C_REFREC,1,5)` is the CADASTRAL code, not INE.** 78 of 82 differ, and they collide
# MAGIC   (cadastral `09155` = Gumiel de Mercado, INE `09155` = Haza). Match on **name**, never code.
# MAGIC - ITACyL serves `ñ` as `%c2%b1` (U+00B1 `±`); folder naming differs by year.
# MAGIC - Median parcel is **0.119 ha**, so spec D5's −15 m buffer + 0.5 ha floor discards 92% of parcels. See Q4.
# MAGIC
# MAGIC ### ⚠️ Licence
# MAGIC ITACyL, every SIGPAC year 2012–2024: *"Está permitido el uso gratuito de los datos, pero queda
# MAGIC prohibida su explotación comercial."* Free use yes, **commercial exploitation no**. Spec §2's
# MAGIC premise that v0.1 is commercially usable because it is public is not currently true.

# COMMAND ----------

# MAGIC %run ../00_setup

# COMMAND ----------

# DBTITLE 1,Parameters
dbutils.widgets.text("do_id", "ribera_del_duero")
dbutils.widgets.text("do_zon_co_id", "49")
dbutils.widgets.text("do_boundary_zip", "calidaddiferenciada_vinos.zip")
dbutils.widgets.text("sigpac_year", "2025")
dbutils.widgets.text("min_frac_inside", "0.01")
dbutils.widgets.text("search_buffer_m", "2000")
dbutils.widgets.text("h3_xwalk_resolution", "13")

DO_ID = dbutils.widgets.get("do_id")
DO_ZON_CO_ID = int(dbutils.widgets.get("do_zon_co_id"))
BOUNDARY_ZIP = f"{VOLUME_PATH}/{dbutils.widgets.get('do_boundary_zip')}"
SIGPAC_YEAR = int(dbutils.widgets.get("sigpac_year"))
SIGPAC_DIR = f"{VOLUME_PATH}/sigpac"
MIN_FRAC_INSIDE = float(dbutils.widgets.get("min_frac_inside"))
SEARCH_BUFFER_M = int(dbutils.widgets.get("search_buffer_m"))
H3_XWALK_RES = int(dbutils.widgets.get("h3_xwalk_resolution"))

dbutils.fs.mkdirs(f"{SIGPAC_DIR}/{SIGPAC_YEAR}")
print(f"{DO_ID} | SIGPAC {SIGPAC_YEAR} | {SIGPAC_DIR}/{SIGPAC_YEAR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 1a — DO boundary
# MAGIC `calidaddiferenciada_vinos.zip` → `ref_do_boundary` (1 row)

# COMMAND ----------

gdf_all = gpd.read_file(f"zip://{BOUNDARY_ZIP}")
gdf_do = gdf_all[gdf_all["ZON_CO_ID"] == DO_ZON_CO_ID].copy()
assert len(gdf_do) == 1, f"expected 1 feature for ZON_CO_ID={DO_ZON_CO_ID}, got {len(gdf_do)}"

if gdf_do.crs is None or gdf_do.crs.to_epsg() != CRS_STORE:
    gdf_do = gdf_do.to_crs(epsg=CRS_STORE)

area_km2 = float(gdf_do.to_crs(epsg=CRS_METRIC).geometry.iloc[0].area / 1e6)
b = gdf_do.total_bounds
print(f"{gdf_do['ZON_DS_NOM'].iloc[0]} | {area_km2:,.1f} km2 | "
      f"bbox [{b[0]:.4f}, {b[1]:.4f}, {b[2]:.4f}, {b[3]:.4f}]")

# Ribera measures 3,243 km2. Wide bound — the real check is the municipality count below.
assert 1_500 <= area_km2 <= 15_000, f"area {area_km2:,.1f} km2 implausible — wrong feature"

spark.createDataFrame(pd.DataFrame({
    "do_id": [DO_ID],
    "do_name": [gdf_do["ZON_DS_NOM"].iloc[0]],
    "zon_co_id": [int(gdf_do["ZON_CO_ID"].iloc[0])],
    "geometry_wkt": [gdf_do.geometry.iloc[0].wkt],
    "area_km2": [area_km2],
    "source": [MAPA_SOURCE],
    "source_vintage": ["2014-03"],
    "extracted_at": [datetime.now(timezone.utc).replace(tzinfo=None)],
})).createOrReplaceTempView("v_boundary")

spark.sql(f"""
    CREATE OR REPLACE TABLE {T_BOUNDARY} AS
    SELECT do_id, do_name, zon_co_id,
           ST_GeomFromText(geometry_wkt, {CRS_STORE}) AS geometry,
           area_km2, source, source_vintage, extracted_at
    FROM v_boundary
""")
display(spark.table(T_BOUNDARY).drop("geometry"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 1b — Municipalities + crosswalk
# MAGIC boundary ∩ `municipios_geo_raw`, then matched to ITACyL's FTP listings → `ref_municipios`
# MAGIC
# MAGIC `frac_inside` is the share of each municipality inside the DO. `min_frac_inside` drops sliver
# MAGIC intersections between two independently digitised datasets.

# COMMAND ----------

spark.sql(f"""
    CREATE OR REPLACE TEMP VIEW v_municipios AS
    WITH isect AS (
        SELECT m.codigo_municipio, m.text AS municipio, m.geometry,
               ST_Area(ST_Transform(m.geometry, {CRS_METRIC})) / 10000.0 AS muni_ha,
               ST_Area(ST_Transform(ST_Intersection(m.geometry, d.geometry), {CRS_METRIC})) / 10000.0 AS inside_ha
        FROM {SOURCE_MUNICIPIOS} m
        JOIN {T_BOUNDARY} d ON ST_Intersects(m.geometry, d.geometry)
    )
    SELECT codigo_municipio, municipio,
           substr(codigo_municipio, 1, 2) AS codigo_provincia,
           geometry, muni_ha, inside_ha,
           inside_ha / muni_ha AS frac_inside,
           (inside_ha / muni_ha) < 0.99 AS is_partial
    FROM isect WHERE inside_ha / muni_ha >= {MIN_FRAC_INSIDE}
""")

munis = spark.sql("SELECT codigo_municipio, codigo_provincia, municipio FROM v_municipios").collect()
provs = sorted({r.codigo_provincia for r in munis} & set(CYL_PROVINCES))
print(f"{len(munis)} municipalities in provinces {provs}")

# COMMAND ----------

# DBTITLE 1,Match to ITACyL FTP listings (by name — never by code)
with cf.ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(list_province_zips, SIGPAC_YEAR, p): p for p in provs}
    listings = {futs[f]: f.result() for f in cf.as_completed(futs)}

rows, missing = [], []
for r in munis:
    hit = listings.get(r.codigo_provincia, {}).get(norm_name(r.municipio))
    if hit is None:
        missing.append((r.codigo_municipio, r.municipio))
    else:
        rows.append((r.codigo_municipio, *hit))

print(f"matched {len(rows)}/{len(munis)}")
if missing:
    print(f"not published by ITACyL for {SIGPAC_YEAR}:")
    for c, n in sorted(missing):
        print(f"   {c}  {n}")
    print("   2022, 2023 and 2025 are complete; 2024 ships only 124 of 225 Valladolid files.")

spark.createDataFrame(
    rows, "codigo_municipio STRING, codigo_catastral STRING, zip_name STRING, url STRING"
).createOrReplaceTempView("v_xwalk")

spark.sql(f"""
    CREATE OR REPLACE TABLE {T_MUNICIPIOS} AS
    SELECT m.codigo_municipio, x.codigo_catastral, m.municipio, m.codigo_provincia,
           m.geometry, m.muni_ha, m.inside_ha, m.frac_inside, m.is_partial,
           (m.codigo_municipio <> x.codigo_catastral) AS codes_differ,
           x.zip_name, x.url, {SIGPAC_YEAR} AS sigpac_year, current_timestamp() AS extracted_at
    FROM v_municipios m JOIN v_xwalk x ON m.codigo_municipio = x.codigo_municipio
""")

# A cadastral code mapping to two INE municipalities would fan out Phase 2's join.
dupes = spark.sql(f"""
    SELECT codigo_catastral FROM {T_MUNICIPIOS}
    GROUP BY codigo_catastral HAVING count(*) > 1
""").count()
assert dupes == 0, f"{dupes} cadastral codes map to multiple INE municipalities"

n_tot = spark.table(T_MUNICIPIOS).count()
n_diff = spark.sql(f"SELECT count(*) c FROM {T_MUNICIPIOS} WHERE codes_differ").first().c
print(f"crosswalk 1:1 ok | {n_diff} of {n_tot} codes differ from INE")
display(spark.table(T_MUNICIPIOS).select(
    "codigo_provincia", "codigo_municipio", "codigo_catastral", "municipio",
    "codes_differ", "frac_inside", "zip_name").orderBy("codigo_municipio"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Q1 — the boundary is a 2014 vintage and is short ~20 municipalities
# MAGIC
# MAGIC | Province | Published | This layer |
# MAGIC |---|---|---|
# MAGIC | Valladolid | 19 | 19 ✓ |
# MAGIC | Segovia | 4 | 4 ✓ |
# MAGIC | Burgos | 60 | 53 |
# MAGIC | **Soria** | **19** | **6** |
# MAGIC | **Total** | **102** | **82** |
# MAGIC
# MAGIC Two provinces match exactly, so this is not a join bug — the 2014 boundary predates ~20
# MAGIC admissions, concentrated in Soria. (Spec §1's "~115" is also wrong; published is 102.)
# MAGIC
# MAGIC `frac_inside` is 1.0 for every municipality, meaning the MAPA polygon is a dissolve of whole
# MAGIC municipal boundaries — so it cannot identify the *paraje* partials either. The fix is to take
# MAGIC the current member list from the BOCyL *pliego* and define the DO as their union.

# COMMAND ----------

display(spark.sql(f"""
    SELECT codigo_provincia, count(*) AS municipios,
           round(min(frac_inside), 4) AS min_frac, round(max(frac_inside), 4) AS max_frac,
           round(sum(inside_ha), 0) AS inside_ha
    FROM {T_MUNICIPIOS} GROUP BY codigo_provincia ORDER BY codigo_provincia
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 1c — STAC search envelope
# MAGIC boundary + 2 km buffer → `ref_search_envelope`
# MAGIC
# MAGIC The hardcoded `[-4.5, 41.4, -3.2, 41.9]` in `01_ingest_imagery` stopped at longitude −3.2 while
# MAGIC the DO reaches −2.894, so ~20% of the denomination (the Soria end) was never searched: 4 tiles
# MAGIC and 45 items for July 2024 instead of 6 tiles and 71. Deriving it here removes that class of bug.

# COMMAND ----------

spark.sql(f"""
    CREATE OR REPLACE TABLE {T_ENVELOPE} AS
    WITH e AS (
        SELECT do_id, ST_Transform(
                   ST_Envelope(ST_Buffer(ST_Transform(geometry, {CRS_METRIC}), {SEARCH_BUFFER_M})),
                   {CRS_STORE}) AS envelope
        FROM {T_BOUNDARY}
    )
    SELECT do_id, envelope, {SEARCH_BUFFER_M} AS buffer_m,
           ST_XMin(envelope) AS west,  ST_YMin(envelope) AS south,
           ST_XMax(envelope) AS east,  ST_YMax(envelope) AS north,
           current_timestamp() AS extracted_at
    FROM e
""")

assert spark.sql(f"""
    SELECT ST_Contains(e.envelope, b.geometry) AS ok
    FROM {T_ENVELOPE} e JOIN {T_BOUNDARY} b ON e.do_id = b.do_id
""").first().ok, "envelope does not contain the boundary"

d = spark.sql(f"""
    SELECT ST_XMax(g) - ST_XMin(g) AS w, ST_YMax(g) - ST_YMin(g) AS h
    FROM (SELECT ST_Transform(envelope, {CRS_METRIC}) AS g FROM {T_ENVELOPE})
""").first()
# A width near 144 km means degrees were multiplied by 111 without the cos(latitude) factor.
assert 100_000 <= d.w <= 145_000 and 40_000 <= d.h <= 80_000, "envelope dimensions implausible"

e = spark.table(T_ENVELOPE).first()
print(f"bbox [{e.west:.4f}, {e.south:.4f}, {e.east:.4f}, {e.north:.4f}] | "
      f"{d.w/1000:.0f} x {d.h/1000:.0f} km")

# COMMAND ----------

# DBTITLE 1,Write the download list
urls = [r.url for r in spark.sql(f"SELECT url FROM {T_MUNICIPIOS} ORDER BY codigo_municipio").collect()]
dbutils.fs.put(f"{SIGPAC_DIR}/sigpac_urls_{SIGPAC_YEAR}.txt", "\n".join(urls) + "\n", overwrite=True)
print(f"{len(urls)} urls -> {SIGPAC_DIR}/sigpac_urls_{SIGPAC_YEAR}.txt")
print(f"Fetch with:  ./scripts/fetch_sigpac.sh {SIGPAC_YEAR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Phase 2 — Load vineyard parcels
# MAGIC `sigpac/{year}/*.zip` → `ref_vineyard_parcels` + `ref_uso_audit`
# MAGIC
# MAGIC Serverless has no heavyweight OGR tier, so this reads shapefiles with GeoPandas rather than
# MAGIC `spark.read.format("gdal")`. Filtered to vineyard codes at read time; the audit is computed
# MAGIC from the unfiltered frame so nothing is dropped without a record.

# COMMAND ----------

# DBTITLE 1,Gate — anything downloaded?
try:
    paths = sorted(to_local_path(f.path)
                   for f in dbutils.fs.ls(f"{SIGPAC_DIR}/{SIGPAC_YEAR}") if f.path.endswith(".zip"))
except Exception:
    paths = []

expected = spark.table(T_MUNICIPIOS).count()
print(f"{len(paths)} / {expected} zips present")

if not paths:
    print(f"\nPhase 1 complete. Download with  ./scripts/fetch_sigpac.sh {SIGPAC_YEAR}  then re-run from here.")
elif len(paths) < expected:
    print(f"! {expected - len(paths)} missing — Phase 2 will load a partial DO.")

# COMMAND ----------

# DBTITLE 1,Read the zips
if paths:
    gdfs, audits, failures = [], [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(read_recintos, p): p for p in paths}
        for fut in cf.as_completed(futs):
            p = futs[fut]
            try:
                audit, vy = fut.result()
                audit["codigo_catastral"] = p.split("/")[-1][:5]
                audits.append(audit)
                if len(vy):
                    gdfs.append(vy)
            except Exception as err:
                failures.append((p.split("/")[-1], str(err)[:120]))

    if failures:
        for fn, msg in failures[:5]:
            print(f"  FAILED {fn}: {msg}")
        # All failing means something systemic (bad path, missing dependency), not bad files.
        if len(failures) == len(paths):
            raise RuntimeError(f"every zip failed. First error: {failures[0][1]}")
    if not gdfs:
        raise RuntimeError("no vineyard parcels found in any zip")

    merged = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
    if merged.crs is None or merged.crs.to_epsg() != CRS_STORE:
        merged = merged.to_crs(epsg=CRS_STORE)

    ref = merged["C_REFREC"].astype(str).str.strip()
    spark.createDataFrame(pd.DataFrame({
        "recinto_id": ref,                  # SIGPAC's own 23-char stable key
        "codigo_catastral": ref.str[:5],    # CADASTRAL — resolved to INE via ref_municipios
        "uso_sigpac": merged["USO_SIGPAC"].astype(str).str.strip(),
        "superficie_m2": merged["SUPERFICIE"].astype(float),   # authoritative m²
        "perimetro_m": merged["PERIMETRO"].astype(float),
        "coef_regadio": merged["COEF_REGAD"].astype(float),    # irrigated vs rainfed
        "poligono": merged["POLIGONO"].astype("int64"),
        "parcela": merged["PARCELA"].astype("int64"),
        "recinto": merged["RECINTO"].astype("int64"),
        "geometry_wkt": merged.geometry.to_wkt(),
    })).createOrReplaceTempView("v_recintos")

    spark.createDataFrame(pd.concat(audits, ignore_index=True)) \
         .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(T_USO_AUDIT)

    print(f"{len(paths)} zips -> {len(merged):,} vineyard-code parcels")

# COMMAND ----------

# DBTITLE 1,Write ref_vineyard_parcels
# uso='VI' AND inside the DO. The clip matters: a bbox around Ribera also contains Cigales and
# Arlanza vineyard, and calling that "Ribera del Duero" would make the product's claim false.
# ST_Intersects (not Within) keeps parcels straddling the border; NB02 decides what to do with them.
#
# ST_Buffer(geom, 0) repairs self-intersections in place — ST_MakeValid is unavailable here.
# Measured: 114 of 69,785 repaired, 0.0% change in area. Dropping them would lose real vineyard.
#
# geom_b05/geom_b10 are the pure-pixel extents NB04 zonal-aggregates against: a pixel centre must
# sit half a pixel from the boundary, so a -5m buffer clears the 10m bands (NDVI) and a -10m buffer
# clears the 20m bands (NDRE, NDMI) — Sentinel-2 resolution, not the -15m Landsat figure in spec D5.
# Buffered geometry is measured in 25830 (metric) then stored back in 4258, per repo convention.
# Parcels that buffer to empty are flagged via reliability_class, not dropped: they are ~60% of
# parcels but only ~10% of area, and dropping them would bias every municipality total toward
# large estates.
#
# h3_cells_res13: parcel<->hex assignment lives here, not in NB04 — it's a pure function of
# parcel geometry (never depends on satellite data), computed once per SIGPAC vintage same as
# geom_b05/geom_b10. Res 13, not res 12: this is an ASSIGNMENT resolution, not a measurement
# resolution — NB04's hex_obs still aggregates satellite pixels at res 12 (matched to the 10 m
# pixel size), but a finer res-13 hex hugs a thin vineyard-strip boundary more tightly, minimizing
# (not eliminating) the case of one hex spanning two parcels. h3_coverash3 (not polyfillash3, which
# is centroid-based and measured to drop 10-12% of thin parcels entirely) against the parcel's
# full unbuffered geometry -- not geom_b05/geom_b10, which stay reserved for zonal stats.
# ref_parcel_h3_xwalk below is a VIEW that explodes this array, not a materialized table: the
# relationship is a fixed fact about the parcel, so there is nothing to separately track staleness
# for. Any res-12 rollup (to join against hex_obs) is likewise left as a query-time
# h3_toparent(h3_cell_id, 12) — free (pure index arithmetic), never precomputed.

if paths:
    spark.sql(f"""
        CREATE OR REPLACE TEMP VIEW v_parcels AS
        SELECT r.recinto_id, m.codigo_municipio, r.codigo_catastral, m.municipio,
               r.uso_sigpac, r.superficie_m2, r.perimetro_m, r.coef_regadio,
               r.poligono, r.parcela, r.recinto,
               ST_GeomFromText(r.geometry_wkt, {CRS_STORE}) AS geom
        FROM v_recintos r
        JOIN {T_MUNICIPIOS} m ON r.codigo_catastral = m.codigo_catastral
        WHERE r.uso_sigpac = '{USO_VINEYARD_PRIMARY}'
    """)

    spark.sql(f"""
        CREATE OR REPLACE TABLE {T_PARCELS} CLUSTER BY (codigo_municipio) AS
        WITH base AS (
            SELECT {SIGPAC_YEAR} AS sigpac_year,
                   p.recinto_id, p.codigo_municipio, p.codigo_catastral, p.municipio,
                   p.uso_sigpac, p.superficie_m2, p.perimetro_m, p.coef_regadio,
                   p.poligono, p.parcela, p.recinto,
                   CASE WHEN ST_IsValid(p.geom) THEN p.geom ELSE ST_Buffer(p.geom, 0) END AS geometry,
                   NOT ST_IsValid(p.geom) AS geometry_repaired
            FROM v_parcels p
            JOIN {T_BOUNDARY} b ON ST_Intersects(p.geom, b.geometry)
        ),
        buffered AS (
            SELECT *,
                   ST_Buffer(ST_Transform(geometry, {CRS_METRIC}), -5) AS geom_b05_m,
                   ST_Buffer(ST_Transform(geometry, {CRS_METRIC}), -10) AS geom_b10_m
            FROM base
        )
        SELECT sigpac_year, recinto_id, codigo_municipio, codigo_catastral, municipio,
               uso_sigpac, superficie_m2, perimetro_m, coef_regadio,
               poligono, parcela, recinto,
               geometry, geometry_repaired,
               ST_Transform(geom_b05_m, {CRS_STORE}) AS geom_b05,
               ST_Transform(geom_b10_m, {CRS_STORE}) AS geom_b10,
               ST_Area(geom_b05_m) AS area_b05_m2,
               ST_Area(geom_b10_m) AS area_b10_m2,
               CASE WHEN ST_Area(geom_b05_m) > 0
                    THEN 2 * ST_Area(geom_b05_m) / ST_Perimeter(geom_b05_m) ELSE 0.0 END AS min_width_b05_m,
               CASE WHEN ST_Area(geom_b10_m) > 0
                    THEN 2 * ST_Area(geom_b10_m) / ST_Perimeter(geom_b10_m) ELSE 0.0 END AS min_width_b10_m,
               ST_Area(geom_b05_m) > 0 AS has_pure_pixel_10m,
               ST_Area(geom_b10_m) > 0 AS has_pure_pixel_20m,
               CASE WHEN ST_Area(geom_b05_m) > 0 THEN 'parcel' ELSE 'aggregate_only' END AS reliability_class,
               h3_coverash3(ST_AsBinary(geometry), {H3_XWALK_RES}) AS h3_cells_res13,
               current_timestamp() AS loaded_at
        FROM buffered
    """)

    # migration guard: an earlier build of NB04 (before this crosswalk moved here) created
    # ref_parcel_h3_xwalk as a TABLE — CREATE OR REPLACE VIEW cannot replace a table in place.
    _existing = spark.sql(f"""
        SELECT table_type FROM {CATALOG}.information_schema.tables
        WHERE table_schema = '{SCHEMA_BRONZE}' AND table_name = 'ref_parcel_h3_xwalk'
    """).collect()
    if _existing and _existing[0].table_type != "VIEW":
        spark.sql(f"DROP TABLE {NS}.ref_parcel_h3_xwalk")

    spark.sql(f"""
        CREATE OR REPLACE VIEW {NS}.ref_parcel_h3_xwalk AS
        SELECT recinto_id, explode(h3_cells_res13) AS h3_cell_id
        FROM {T_PARCELS}
    """)

    display(spark.sql(f"""
        SELECT count(*) AS parcels, count(DISTINCT codigo_municipio) AS municipios,
               sum(CASE WHEN geometry_repaired THEN 1 ELSE 0 END) AS repaired,
               round(sum(superficie_m2)/10000, 1) AS ha,
               round(median(superficie_m2)/10000, 3) AS median_ha,
               sum(CASE WHEN has_pure_pixel_10m THEN 1 ELSE 0 END) AS has_pure_pixel_10m_n,
               sum(CASE WHEN reliability_class = 'parcel' THEN 1 ELSE 0 END) AS reliability_parcel_n,
               round(avg(size(h3_cells_res13)), 1) AS avg_h3_cells_res13
        FROM {T_PARCELS}
    """))

# COMMAND ----------

# MAGIC %md
# MAGIC ## QA

# COMMAND ----------

# DBTITLE 1,Q2 — geometry validity (raises) and CRS sanity
# SIGPAC ships polygons in degrees while SUPERFICIE is m². If the 25830 reprojection were wrong
# these two would diverge immediately, so this is the cheapest possible CRS check.

if paths:
    bad = spark.sql(f"SELECT count(*) c FROM {T_PARCELS} WHERE NOT ST_IsValid(geometry)").first().c
    assert bad == 0, f"{bad} geometries still invalid after repair — investigate before NB04"
    print("Q2 geometry ok")

    display(spark.sql(f"""
        SELECT round(sum(superficie_m2)/10000, 1) AS declared_ha,
               round(sum(ST_Area(ST_Transform(geometry, {CRS_METRIC})))/10000, 1) AS computed_ha,
               round(100 * (sum(ST_Area(ST_Transform(geometry, {CRS_METRIC}))) - sum(superficie_m2))
                     / sum(superficie_m2), 3) AS pct_diff
        FROM {T_PARCELS}
    """))
    print("expect |pct_diff| well under 1%")

# COMMAND ----------

# DBTITLE 1,Q3 — land-use audit (the VI-only decision)
# Spec §8 NB01.4: if the vineyard-association codes are under 1% of area, exclude them and never
# revisit. ref_uso_audit is the permanent record. Note the spec's list omits CV.

if paths:
    codes = ", ".join(f"'{u}'" for u in USO_VINEYARD_ALL)
    display(spark.sql(f"""
        WITH t AS (SELECT USO_SIGPAC AS uso, sum(sum_superficie_m2)/10000 AS ha
                   FROM {T_USO_AUDIT} WHERE USO_SIGPAC IN ({codes})
                   GROUP BY USO_SIGPAC)
        SELECT uso, round(ha, 1) AS ha, round(100 * ha / sum(ha) OVER (), 3) AS pct_of_vineyard
        FROM t ORDER BY ha DESC
    """))

# COMMAND ----------

# DBTITLE 1,Q4 — attrition ladder (sets NB02's thresholds)
# MEASURED: spec D5 does not survive the data.
#   raw            69,785 parcels   30,721 ha   100%
#   -15 m buffer   24,333 (35%)     13,352 ha    43%
#   + 0.5 ha floor  5,838 ( 8%)     11,196 ha    36%
# It discards 92% of parcels and 64% of area, because the median parcel is 0.119 ha (~34x34 m)
# and a -15 m inward buffer erases it. NB02 options: shrink the buffer to -5/-10 m (one S2 pixel),
# drop the area floor and rely on H3 coverage_fraction weighting, or accept ~36% coverage.

if paths:
    display(spark.sql(f"""
        WITH b AS (SELECT superficie_m2,
                          ST_Area(ST_Buffer(ST_Transform(geometry, {CRS_METRIC}), -15)) AS buf_m2
                   FROM {T_PARCELS})
        SELECT count(*) AS parcels,
               round(sum(superficie_m2)/10000, 1) AS ha_raw,
               sum(CASE WHEN buf_m2 > 0 THEN 1 ELSE 0 END) AS survive_buffer,
               round(sum(buf_m2)/10000, 1) AS ha_after_buffer,
               sum(CASE WHEN buf_m2 >= 5000 THEN 1 ELSE 0 END) AS survive_both,
               round(sum(CASE WHEN buf_m2 >= 5000 THEN buf_m2 ELSE 0 END)/10000, 1) AS ha_final,
               round(100.0 * sum(CASE WHEN buf_m2 >= 5000 THEN buf_m2 ELSE 0 END)
                     / sum(superficie_m2), 1) AS pct_area_retained
        FROM b
    """))

# COMMAND ----------

# DBTITLE 1,Q5 — total vs the Consejo, and per municipality
# Consejo Regulador 2025: 27,468.59 ha inscribed. SIGPAC should EXCEED it — it records vineyard
# never registered with the Consejo. But the 2014 boundary covers only 82 of 102 municipalities,
# which pushes the other way, so read the ratio as a sanity check rather than a precise gate.

if paths:
    display(spark.sql(f"""
        SELECT round(sum(superficie_m2)/10000, 1) AS sigpac_vi_ha,
               27468.59 AS consejo_inscribed_ha,
               round(sum(superficie_m2)/10000 / 27468.59, 3) AS ratio
        FROM {T_PARCELS}
    """))
    display(spark.sql(f"""
        SELECT codigo_municipio, municipio, count(*) AS parcels,
               round(sum(superficie_m2)/10000, 1) AS vi_ha
        FROM {T_PARCELS} GROUP BY codigo_municipio, municipio ORDER BY vi_ha DESC
    """))
    print("cross-check the top rows against the Consejo's 'Superficie de Viñedo por Municipio'")

# COMMAND ----------

# DBTITLE 1,Q6 — h3_cells_res13 / ref_parcel_h3_xwalk sanity
# coverash3 measured to drop 0% of parcels (vs. 10-12% for polyfillash3) — any parcel with zero
# cells here is a regression, not an expected edge case. raise, not informational.

if paths:
    empty = spark.sql(f"""
        SELECT count(*) c FROM {T_PARCELS} WHERE h3_cells_res13 IS NULL OR size(h3_cells_res13) = 0
    """).first().c
    assert empty == 0, f"{empty} parcels have zero h3_cells_res13 — coverash3 regression, investigate"

    display(spark.sql(f"""
        SELECT count(*) AS xwalk_rows, count(DISTINCT recinto_id) AS parcels,
               count(DISTINCT h3_cell_id) AS distinct_hexes
        FROM {NS}.ref_parcel_h3_xwalk
    """))
    print("Q6 ok — 0 parcels with an empty h3_cells_res13 array")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC
# MAGIC 1. **Re-run `01_ingest_imagery`** — it now reads its bbox from `ref_search_envelope`, adding
# MAGIC    tiles 30TWL/30TWM that the old hardcoded bbox missed.
# MAGIC 2. **NB02 `02_vineyard_mask`** — buffer, area floor, QA gate S2. Set thresholds from Q4, not
# MAGIC    from the spec. Spec D4 (fixed mask across years) is deferred: one snapshot is enough while
# MAGIC    the register is frozen; load a second `sigpac_year` if phenology shows cadastre-driven steps.
# MAGIC 3. **Boundary** — 82 of 102 municipalities. Take the current member list from the BOCyL *pliego*.
# MAGIC 4. **Licence** — commercial exploitation prohibited; unresolved.

# COMMAND ----------

