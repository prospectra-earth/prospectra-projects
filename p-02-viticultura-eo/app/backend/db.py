from contextlib import contextmanager

from databricks import sql
from databricks.sdk.core import Config

from . import config

_cfg = Config()


@contextmanager
def get_connection():
    if not config.HTTP_PATH:
        raise RuntimeError(
            "DATABRICKS_WAREHOUSE_ID is not set — check app.yaml resources/env binding."
        )
    connection = sql.connect(
        server_hostname=_cfg.host,
        http_path=config.HTTP_PATH,
        credentials_provider=lambda: _cfg.authenticate,
        catalog=config.CATALOG,
        schema=config.SCHEMA,
    )
    try:
        yield connection
    finally:
        connection.close()


def run_query(query: str, params: dict | None = None) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params or {})
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]


def run_query_one(query: str, params: dict | None = None) -> dict | None:
    rows = run_query(query, params)
    return rows[0] if rows else None
