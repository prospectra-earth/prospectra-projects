from cachetools import TTLCache, cached
from cachetools.keys import hashkey

from . import config

_seasons_cache = TTLCache(maxsize=1, ttl=config.CACHE_TTL_LONG)
_municipios_cache = TTLCache(maxsize=1, ttl=config.CACHE_TTL_LONG)
_dates_cache = TTLCache(maxsize=64, ttl=config.CACHE_TTL_SHORT)
_boundary_cache = TTLCache(maxsize=1, ttl=config.CACHE_TTL_FOREVER)
_status_cache = TTLCache(maxsize=1, ttl=60)  # short — this is the "is data live" pill


def cached_seasons(fn):
    return cached(cache=_seasons_cache)(fn)


def cached_municipios(fn):
    return cached(cache=_municipios_cache)(fn)


def cached_dates(fn):
    return cached(cache=_dates_cache, key=lambda season, grain: hashkey(season, grain))(fn)


def cached_boundary(fn):
    return cached(cache=_boundary_cache)(fn)


def cached_status(fn):
    return cached(cache=_status_cache)(fn)
