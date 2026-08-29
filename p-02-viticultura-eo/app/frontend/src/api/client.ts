import type {
  BoundaryOut,
  DateOut,
  HexCellGeometryOut,
  HexCellTimeseriesPointOut,
  HexCellValueOut,
  ListResponse,
  MunicipioOut,
  ParcelGeometryOut,
  ParcelTimeseriesPointOut,
  ParcelValueOut,
  SeasonOut,
  StatusOut,
} from "./types";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function getJSON<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") url.searchParams.set(key, value);
    }
  }
  const res = await fetch(url.pathname + url.search);
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // no JSON body
    }
    const detail =
      body && typeof body === "object" && "detail" in body ? String((body as { detail: unknown }).detail) : res.statusText;
    throw new ApiError(res.status, detail, body);
  }
  return res.json();
}

export const api = {
  seasons: () => getJSON<ListResponse<SeasonOut>>("/api/meta/seasons"),
  municipios: () => getJSON<ListResponse<MunicipioOut>>("/api/meta/municipios"),
  dates: (season: number, grain: "h3" | "parcel") =>
    getJSON<ListResponse<DateOut>>("/api/meta/dates", { season: String(season), grain }),
  status: () => getJSON<StatusOut>("/api/meta/status"),
  doBoundary: () => getJSON<BoundaryOut>("/api/do-boundary"),

  hexGeometry: (season: number, municipioCodes: string[]) =>
    getJSON<ListResponse<HexCellGeometryOut>>("/api/hex-cells/geometry", {
      season: String(season),
      municipio_codes: municipioCodes.join(","),
    }),
  hexValues: (season: number, municipioCodes: string[]) =>
    getJSON<ListResponse<HexCellValueOut>>("/api/hex-cells/values", {
      season: String(season),
      municipio_codes: municipioCodes.join(","),
    }),
  hexTimeseries: (h3CellId: string, season: number) =>
    getJSON<ListResponse<HexCellTimeseriesPointOut>>(`/api/hex-cells/${encodeURIComponent(h3CellId)}/timeseries`, {
      season: String(season),
    }),

  parcelGeometry: (municipioCodes: string[]) =>
    getJSON<ListResponse<ParcelGeometryOut>>("/api/parcels/geometry", {
      municipio_codes: municipioCodes.join(","),
    }),
  parcelValues: (season: number, municipioCodes: string[]) =>
    getJSON<ListResponse<ParcelValueOut>>("/api/parcels/values", {
      season: String(season),
      municipio_codes: municipioCodes.join(","),
    }),
  parcelTimeseries: (recintoId: string, season: number) =>
    getJSON<ListResponse<ParcelTimeseriesPointOut>>(`/api/parcels/${encodeURIComponent(recintoId)}/timeseries`, {
      season: String(season),
    }),
};
