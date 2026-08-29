import type { Geometry } from "./api/types";
import type { ReliabilityClass } from "./state/useFilters";

/** Grain-agnostic shape the map/inspector/stat-strip consume — a join of geometry + the current obs_date's value row. */
export interface MapItem {
  id: string;
  municipio: string;
  codigoMunicipio: string;
  boundary: Geometry;
  areaHa: number | null;
  reliabilityClass: ReliabilityClass | null;
  ndviMean: number | null;
  ndreMean: number | null;
  ndmiMean: number | null;
  pctValid: number | null;
  nPx: number | null;
  hasValue: boolean;
}
