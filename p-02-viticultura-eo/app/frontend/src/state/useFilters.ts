import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export type Grain = "h3" | "parcel";
export type IndexKey = "ndvi" | "ndre" | "ndmi";
export type ReliabilityClass = "parcel" | "aggregate_only";

export interface Filters {
  grain: Grain;
  index: IndexKey;
  season: number | undefined;
  municipioCodes: string[];
  qualityOverlay: boolean;
  reliability: ReliabilityClass[];
  obsDate: string | undefined;
  selectedId: string | undefined;
}

export const DEFAULT_RELIABILITY: ReliabilityClass[] = ["parcel", "aggregate_only"];

type Patch = Partial<Record<"grain" | "index" | "season" | "municipio_codes" | "quality_overlay" | "reliability" | "obs_date" | "selected_id", string | null>>;

export function useFilters() {
  const [params, setParams] = useSearchParams();

  const filters: Filters = useMemo(() => {
    const grain = (params.get("grain") as Grain) || "h3";
    const index = (params.get("index") as IndexKey) || "ndvi";
    const seasonRaw = params.get("season");
    const season = seasonRaw ? Number(seasonRaw) : undefined;
    const municipioCodes = (params.get("municipio_codes") || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const qualityOverlay = params.get("quality_overlay") === "1";
    const reliabilityRaw = params.get("reliability");
    const reliability = reliabilityRaw
      ? (reliabilityRaw.split(",").filter(Boolean) as ReliabilityClass[])
      : DEFAULT_RELIABILITY;
    const obsDate = params.get("obs_date") || undefined;
    const selectedId = params.get("selected_id") || undefined;
    return { grain, index, season, municipioCodes, qualityOverlay, reliability, obsDate, selectedId };
  }, [params]);

  const update = useCallback(
    (patch: Patch) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            if (value === null || value === undefined || value === "") next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return { filters, update };
}
