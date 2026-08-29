import os

CATALOG = "winery_satellite"
SCHEMA = "bronze"          # default namespace — reference tables live here
NS = f"{CATALOG}.{SCHEMA}"

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID")
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST")
HTTP_PATH = f"/sql/1.0/warehouses/{WAREHOUSE_ID}" if WAREHOUSE_ID else None

APP_PORT = int(os.environ.get("DATABRICKS_APP_PORT", 8000))

# in-process meta cache TTLs (seconds)
CACHE_TTL_LONG = 60 * 60       # seasons, municipios — changes only on rare NB run
CACHE_TTL_SHORT = 15 * 60      # dates — fine to refresh more often
CACHE_TTL_FOREVER = 60 * 60 * 24 * 7  # do-boundary — effectively immutable

H3_RES_DEFAULT = 12  # notebook widget default, not a hard guarantee — surfaced via /api/meta, never hardcoded in copy
