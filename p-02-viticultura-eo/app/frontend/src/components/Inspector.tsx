import { DOMAINS, fmt } from "../domains";
import type { Grain, IndexKey } from "../state/useFilters";
import type { MapItem } from "../types";

export interface SeriesPoint {
  obsDate: string;
  doy: number;
  mean: number | null;
  p50: number | null;
  std: number | null;
}

interface Props {
  grain: Grain;
  index: IndexKey;
  season: number | undefined;
  obsDate: string | undefined;
  item: MapItem | undefined;
  series: SeriesPoint[];
  loading: boolean;
  onClose: () => void;
}

const CloseIcon = () => (
  <svg viewBox="0 0 16 16" stroke="currentColor" strokeWidth={1.6} fill="none">
    <line x1="3" y1="3" x2="13" y2="13" />
    <line x1="13" y1="3" x2="3" y2="13" />
  </svg>
);

function clamp(x: number, a: number, b: number) {
  return Math.max(a, Math.min(b, x));
}

export default function Inspector({ grain, index, season, obsDate, item, series, loading, onClose }: Props) {
  if (!item) return null;
  const domain = DOMAINS[index];
  const isParcel = grain === "parcel";

  const W = 284;
  const H = 64;
  const pad = 4;
  const pts = series.map((s, i) => {
    const x = pad + (series.length > 1 ? i / (series.length - 1) : 0) * (W - pad * 2);
    const v = s.mean ?? domain.min;
    const t = clamp((v - domain.min) / (domain.max - domain.min), 0, 1);
    const y = H - pad - t * (H - pad * 2);
    return [x, y] as [number, number];
  });
  const linePath = pts.length ? `M ${pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" L ")}` : "";
  const areaPath = pts.length
    ? `${linePath} L ${pts[pts.length - 1][0].toFixed(1)},${H - pad} L ${pts[0][0].toFixed(1)},${H - pad} Z`
    : "";

  const currentIdx = series.findIndex((s) => s.obsDate === obsDate);
  const currentPt = currentIdx >= 0 ? pts[currentIdx] : pts[pts.length - 1];
  const currentSeries = currentIdx >= 0 ? series[currentIdx] : series[series.length - 1];

  return (
    <aside className="inspector">
      <div className="inspector-head">
        <div>
          <div className="inspector-id">{item.id}</div>
          <div className="inspector-kind">{isParcel ? "SIGPAC parcel" : "H3 res-12 cell"}</div>
        </div>
        <button className="closebtn" onClick={onClose}>
          <CloseIcon />
        </button>
      </div>

      <div className="inspector-row">
        <span className="k">Municipio</span>
        <span className="v">{item.municipio}</span>
      </div>
      <div className="inspector-row">
        <span className="k">Area</span>
        <span className="v">{item.areaHa !== null ? `${fmt(item.areaHa, 2)} ha` : "—"}</span>
      </div>

      {isParcel && (
        <>
          <div className="inspector-row">
            <span className="k">reliability_class</span>
            <span className={`badge ${item.reliabilityClass === "parcel" ? "good" : "warn"}`}>
              {item.reliabilityClass ?? "—"}
            </span>
          </div>
          <div className="inspector-row">
            <span className="k">pct_valid</span>
            <span className="v">{item.pctValid !== null ? `${Math.round(item.pctValid * 100)}%` : "—"}</span>
          </div>
        </>
      )}

      <div className="spark-section">
        <div className="spark-title">
          {domain.label} time series — season {season}
        </div>
        {loading ? (
          <div className="spark-empty">Loading…</div>
        ) : series.length === 0 ? (
          <div className="spark-empty">No observations for this season yet.</div>
        ) : (
          <>
            <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
              <path d={areaPath} fill="var(--seq-6)" opacity={0.1} />
              <path d={linePath} fill="none" stroke="var(--seq-6)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
              {currentPt && (
                <circle cx={currentPt[0]} cy={currentPt[1]} r={4} fill="var(--accent)" stroke="var(--paper)" strokeWidth={2} />
              )}
            </svg>
            <div className="spark-values">
              <div className="spark-stat">
                <div className="k">mean</div>
                <div className="v">{fmt(currentSeries?.mean, 2)}</div>
              </div>
              <div className="spark-stat">
                <div className="k">p50</div>
                <div className="v">{fmt(currentSeries?.p50, 2)}</div>
              </div>
              <div className="spark-stat">
                <div className="k">std</div>
                <div className="v">{fmt(currentSeries?.std, 2)}</div>
              </div>
            </div>
          </>
        )}
      </div>

      <div className="inspector-note">
        {isParcel
          ? "Values are per-parcel zonal stats over the pure-pixel buffer (geom_b05/geom_b10)."
          : "hex_obs carries mean index + pixel counts; no recinto-level reliability at this grain."}
      </div>
    </aside>
  );
}
