# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 00 — Setup
# MAGIC
# MAGIC Dependencies, shared config and helpers for `viticultura-eo`.
# MAGIC Every other notebook starts with `%run ./00_setup`.
# MAGIC
# MAGIC **Outputs:** constants and functions in the caller's namespace. Writes nothing.

# COMMAND ----------

# MAGIC %pip install pystac-client geopandas pyogrio shapely --quiet

# COMMAND ----------

# DBTITLE 1,Imports
import io
import json
import re
import unicodedata
import urllib.parse
import urllib.request
import warnings
import zipfile
import concurrent.futures as cf
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd
from pyspark.sql import functions as SF
from pyspark.sql.functions import col, lit

# COMMAND ----------

# DBTITLE 1,Constants
CATALOG = "winery_satellite"
SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"
NS = f"{CATALOG}.{SCHEMA_BRONZE}"          # default namespace — most tables live here
NS_SILVER = f"{CATALOG}.{SCHEMA_SILVER}"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/raw"

# CRS: store geographic, measure metric. ST_Area on 4258 returns square degrees.
CRS_STORE = 4258    # ETRS89 geographic — all three vector sources use it
CRS_METRIC = 25830  # ETRS89 / UTM 30N — the only CRS we measure in

# Tables
T_BOUNDARY = f"{NS}.ref_do_boundary"
T_MUNICIPIOS = f"{NS}.ref_municipios"
T_ENVELOPE = f"{NS}.ref_search_envelope"
T_PARCELS = f"{NS}.ref_vineyard_parcels"
T_USO_AUDIT = f"{NS}.ref_uso_audit"
T_STAC = f"{NS}.stac_items"

SOURCE_MUNICIPIOS = "winery_satellite.provided.municipios_geo_raw"

# Sources
MAPA_SOURCE = "https://www.mapa.gob.es/es/cartografia-y-sig/ide/descargas/alimentacion/vinos"
ITACYL_BASE = "https://ftp.itacyl.es/cartografia/05_SIGPAC"
STAC_API_URL = "https://earth-search.aws.element84.com/v1"

# sentinel-2-l2a, NOT c1-l2a: Collection 1 returns zero items over Ribera for 2022.
S2_COLLECTION = "sentinel-2-l2a"

S2_BANDS = {
    "red":      "B04  red        10m  NDVI",
    "nir":      "B08  NIR        10m  NDVI",
    "rededge1": "B05  red-edge   20m  NDRE",
    "nir08":    "B8A  NIR narrow 20m  NDRE, NDMI",
    "swir16":   "B11  SWIR       20m  NDMI",
    "scl":      "SCL  class map  20m  cloud/shadow mask",
}

# ITACyL FTP folder names by INE province code (Castilla y León).
CYL_PROVINCES = {
    "05": "Avila", "09": "Burgos", "24": "Leon", "34": "Palencia",
    "37": "Salamanca", "40": "Segovia", "42": "Soria", "47": "Valladolid",
    "49": "Zamora",
}

USO_VINEYARD_PRIMARY = "VI"                       # viñedo
USO_VINEYARD_ADJACENT = ["VF", "VO", "FV", "CV"]  # viñedo-frutal, -olivar, frutos secos-, cítricos-
USO_VINEYARD_ALL = [USO_VINEYARD_PRIMARY] + USO_VINEYARD_ADJACENT

spark.sql(f"USE CATALOG {CATALOG}")
spark.sql(f"USE SCHEMA {SCHEMA_BRONZE}")

# COMMAND ----------

# DBTITLE 1,Helpers — paths and names
def to_local_path(p: str) -> str:
    """dbutils.fs.ls returns 'dbfs:/Volumes/...'; UC Volumes live at /Volumes/, not /dbfs/."""
    return p[5:] if p.startswith("dbfs:") else p


def norm_name(s: str) -> str:
    """
    Normalise a municipality name for cross-source matching: percent-encoding,
    ITACyL's mojibake n-tilde (served as U+00B1), accents, punctuation, and the
    leading article INE keeps ('La Horra') but ITACyL drops ('Horra').
    """
    s = urllib.parse.unquote(str(s))
    s = s.replace("±", "n").replace("ñ", "n").replace("Ñ", "n")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    m = re.match(r"^(.*),\s*(el|la|los|las)$", s)
    if m:
        s = f"{m.group(2)} {m.group(1)}"
    return re.sub(r"^(el|la|los|las)\s+", "", s)

# COMMAND ----------

# DBTITLE 1,Helpers — SIGPAC
def list_province_zips(year: int, prov_code: str) -> dict:
    """
    {normalised_name: (cadastral_code, filename, url)} from the ITACyL FTP listing.

    Tries both folder conventions: 2023-2025 use 'Burgos/', 2022 uses '09_Burgos/'.
    Trying only one yields a silent 404 and an empty year.
    """
    name = CYL_PROVINCES[prov_code]
    base = f"{ITACYL_BASE}/{year}_ETRS89/Parcelario_SIGPAC_CyL_Municipios"
    for folder in (name, f"{prov_code}_{name}"):
        try:
            with urllib.request.urlopen(f"{base}/{folder}/", timeout=90) as r:
                html = r.read().decode("utf-8", errors="replace")
        except Exception:
            continue
        found = re.findall(r'href="((\d{5})_([^"]*?)\.zip)"', html)
        if found:
            url = f"{base}/{folder}/"
            return {norm_name(nm): (code, fn, url + fn) for fn, code, nm in found}
    print(f"  ! {year} {name}: no listing found")
    return {}


def read_recintos(zip_path: str):
    """One SIGPAC municipality zip -> (uso_audit_df, vineyard_gdf).

    Audit aggregates over ALL land uses before filtering, so the exclusion is evidenced.
    """
    local = to_local_path(zip_path)
    with zipfile.ZipFile(io.BytesIO(open(local, "rb").read())) as z:
        members = [n for n in z.namelist() if n.upper().endswith("_RECFE.SHP")]
        if not members:
            raise FileNotFoundError(f"no *_RECFE.shp inside {zip_path}")
        member = members[0]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gdf = gpd.read_file(f"zip://{local}!{member}")

    audit = (gdf.groupby("USO_SIGPAC")
                .agg(n_recintos=("USO_SIGPAC", "size"), sum_superficie_m2=("SUPERFICIE", "sum"))
                .reset_index())
    return audit, gdf[gdf["USO_SIGPAC"].isin(USO_VINEYARD_ALL)].copy()

# COMMAND ----------

# DBTITLE 1,Helpers — STAC
def extract_band_rows(items, bands, do_id, collection, season, ingested_at=None):
    """
    STAC items -> one row per (item, band).

    scale/offset are read from raster:bands PER ITEM PER BAND and never inferred from
    date or processing baseline: ESA reprocessed parts of the archive, so baseline is
    not monotonic in time (a 2019 scene carries 05.00 and needs the offset; a 2021
    scene carries 03.01 and must not have it).
    """
    ingested_at = ingested_at or datetime.now(timezone.utc)
    rows = []
    for it in items:
        p = it.properties
        common = {
            "do_id": do_id,
            "collection": collection,
            "item_id": it.id,
            "season": season,
            "obs_datetime": it.datetime.replace(tzinfo=None) if it.datetime else None,
            "obs_date": it.datetime.date() if it.datetime else None,
            "doy": it.datetime.timetuple().tm_yday if it.datetime else None,
            "tile": p.get("grid:code") or p.get("s2:mgrs_tile"),
            "platform": p.get("platform"),
            "proj_epsg": p.get("proj:epsg") or p.get("proj:code"),
            "eo_cloud_cover": p.get("eo:cloud_cover"),
            "s2_processing_baseline": p.get("s2:processing_baseline"),
            "s2_degraded_pct": p.get("s2:degraded_msi_data_percentage"),
            "footprint_geojson": json.dumps(it.geometry),
            "ingested_at": ingested_at,
        }
        for band_key, band_desc in bands.items():
            asset = it.assets.get(band_key)
            if asset is None:
                continue
            rb = (asset.extra_fields.get("raster:bands") or [{}])[0]
            rows.append({
                **common,
                "band": band_key,
                "band_desc": band_desc,
                "href": asset.href,
                "media_type": asset.media_type,
                "scale": rb.get("scale"),      # reflectance = DN * scale + offset
                "offset": rb.get("offset"),
                "nodata": rb.get("nodata"),
                "data_type": rb.get("data_type"),
                "spatial_resolution": rb.get("spatial_resolution"),
            })
    return rows

# COMMAND ----------

print(f"setup ready | {NS} / {NS_SILVER} | store CRS {CRS_STORE}, measure CRS {CRS_METRIC}")