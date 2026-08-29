import { useQuery } from "@tanstack/react-query";
import type { Grain } from "../state/useFilters";
import { ApiError, api } from "./client";

const notReadyRetry = (failureCount: number, error: unknown) => {
  if (error instanceof ApiError && error.status === 503) return false;
  return failureCount < 2;
};

export function useSeasons() {
  return useQuery({ queryKey: ["seasons"], queryFn: api.seasons, staleTime: 60_000 });
}

export function useMunicipios() {
  return useQuery({ queryKey: ["municipios"], queryFn: api.municipios, staleTime: 60_000 });
}

export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 60_000 });
}

export function useDoBoundary() {
  return useQuery({ queryKey: ["do-boundary"], queryFn: api.doBoundary, staleTime: Infinity });
}

export function useDates(season: number | undefined, grain: Grain) {
  return useQuery({
    queryKey: ["dates", season, grain],
    queryFn: () => api.dates(season as number, grain),
    enabled: season !== undefined,
  });
}

export function useHexGeometry(season: number | undefined, municipioCodes: string[]) {
  return useQuery({
    queryKey: ["hex-geometry", season, municipioCodes],
    queryFn: () => api.hexGeometry(season as number, municipioCodes),
    enabled: season !== undefined && municipioCodes.length > 0,
  });
}

export function useHexValues(season: number | undefined, municipioCodes: string[]) {
  return useQuery({
    queryKey: ["hex-values", season, municipioCodes],
    queryFn: () => api.hexValues(season as number, municipioCodes),
    enabled: season !== undefined && municipioCodes.length > 0,
  });
}

export function useHexTimeseries(h3CellId: string | undefined, season: number | undefined) {
  return useQuery({
    queryKey: ["hex-timeseries", h3CellId, season],
    queryFn: () => api.hexTimeseries(h3CellId as string, season as number),
    enabled: !!h3CellId && season !== undefined,
  });
}

export function useParcelGeometry(municipioCodes: string[]) {
  return useQuery({
    queryKey: ["parcel-geometry", municipioCodes],
    queryFn: () => api.parcelGeometry(municipioCodes),
    enabled: municipioCodes.length > 0,
    retry: notReadyRetry,
  });
}

export function useParcelValues(season: number | undefined, municipioCodes: string[]) {
  return useQuery({
    queryKey: ["parcel-values", season, municipioCodes],
    queryFn: () => api.parcelValues(season as number, municipioCodes),
    enabled: season !== undefined && municipioCodes.length > 0,
    retry: notReadyRetry,
  });
}

export function useParcelTimeseries(recintoId: string | undefined, season: number | undefined) {
  return useQuery({
    queryKey: ["parcel-timeseries", recintoId, season],
    queryFn: () => api.parcelTimeseries(recintoId as string, season as number),
    enabled: !!recintoId && season !== undefined,
    retry: notReadyRetry,
  });
}
