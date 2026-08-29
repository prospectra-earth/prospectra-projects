export type Geometry = { type: string; coordinates: unknown };

export interface Meta {
  row_count: number;
}

export interface ListResponse<T> {
  data: T[];
  meta: Meta;
}

// ---- meta ----

export interface SeasonOut {
  season: number;
  grains_available: string[];
  n_dates: number;
}

export interface MunicipioOut {
  codigo_municipio: string;
  municipio: string;
  n_parcels: number;
}

export interface DateOut {
  obs_date: string;
  doy: number;
  item_id: string;
  tile: string;
}

export interface StatusOut {
  warehouse_connected: boolean;
  latest_hex_processed_at: string | null;
  hex_obs_row_count: number;
  parcel_obs_available: boolean;
  parcel_obs_row_count: number | null;
}

export interface BoundaryOut {
  boundary_geojson: Geometry;
}

// ---- H3 grain ----

export interface HexCellGeometryOut {
  h3_cell_id: string;
  codigo_municipio: string;
  municipio: string;
  boundary_geojson: Geometry;
}

export interface HexCellValueOut {
  h3_cell_id: string;
  obs_date: string;
  doy: number;
  ndvi_mean: number | null;
  ndre_mean: number | null;
  ndmi_mean: number | null;
  n_px_ndvi: number | null;
  n_px_ndre: number | null;
  n_px_ndmi: number | null;
}

export interface HexCellTimeseriesPointOut {
  obs_date: string;
  doy: number;
  ndvi_mean: number | null;
  ndre_mean: number | null;
  ndmi_mean: number | null;
  n_px_ndvi: number | null;
  n_px_ndre: number | null;
  n_px_ndmi: number | null;
}

// ---- parcel grain ----

export interface ParcelGeometryOut {
  recinto_id: string;
  codigo_municipio: string;
  municipio: string;
  area_ha: number;
  reliability_class: string;
  boundary_geojson: Geometry;
}

export interface ParcelValueOut {
  recinto_id: string;
  obs_date: string;
  doy: number;
  ndvi_mean: number | null;
  ndvi_p50: number | null;
  ndvi_std: number | null;
  ndre_mean: number | null;
  ndre_p50: number | null;
  ndre_std: number | null;
  ndmi_mean: number | null;
  ndmi_p50: number | null;
  pct_valid: number | null;
  n_px_valid: number | null;
  n_px_total: number | null;
}

export interface ParcelTimeseriesPointOut {
  obs_date: string;
  doy: number;
  ndvi_mean: number | null;
  ndvi_p50: number | null;
  ndvi_std: number | null;
  ndre_mean: number | null;
  ndre_p50: number | null;
  ndre_std: number | null;
  ndmi_mean: number | null;
  ndmi_p50: number | null;
  pct_valid: number | null;
  reliability_class: string;
}
