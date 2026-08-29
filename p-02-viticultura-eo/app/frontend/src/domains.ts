import type { IndexKey } from "./state/useFilters";

// Fixed agronomic domains (not data-driven min/max) — the same absolute vine-health
// value always renders the same color regardless of what's filtered in.
export const DOMAINS: Record<IndexKey, { min: number; max: number; label: string; hint: string }> = {
  ndvi: {
    min: 0.1,
    max: 0.8,
    label: "NDVI",
    hint: "Chlorophyll / canopy density — from geom_b05, 10 m bands.",
  },
  ndre: {
    min: 0.05,
    max: 0.55,
    label: "NDRE",
    hint: "Red-edge, saturates later on dense canopy — preferred near peak.",
  },
  ndmi: {
    min: -0.1,
    max: 0.35,
    label: "NDMI",
    hint: "Canopy moisture, SWIR-based — from geom_b10, 20 m bands.",
  },
};

// Sequential ramp — literal hex, mirrored from the design tokens (Plotly can't read CSS vars).
export const SEQ_RAMP_LIGHT = ["#def4de", "#b7dfb7", "#8fc690", "#69ab6c", "#478f4b", "#23712b", "#005010"];
export const SEQ_RAMP_DARK = ["#133716", "#1d5522", "#2b7532", "#3f9446", "#59b15e", "#7cca7f", "#a5e0a5"];

export const STATUS_GOOD = "#0ca30c";
export const STATUS_WARNING = "#fab219";

export function prefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

const MONTH_LEN = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function doyToLabel(doy: number): string {
  let d = doy;
  let m = 0;
  while (m < 12 && d > MONTH_LEN[m]) {
    d -= MONTH_LEN[m];
    m++;
  }
  return `${d} ${MONTH_ABBR[m]}`;
}

export function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}
