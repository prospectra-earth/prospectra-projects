import { DOMAINS, doyToLabel, fmt } from "../domains";
import type { Grain, IndexKey } from "../state/useFilters";

interface Props {
  index: IndexKey;
  grain: Grain;
  qualityOverlay: boolean;
  doy: number | null;
  season: number | undefined;
}

export default function Legend({ index, grain, qualityOverlay, doy, season }: Props) {
  const domain = DOMAINS[index];
  const showQuality = qualityOverlay && grain === "parcel";
  const title = showQuality
    ? "Reliability — parcel_obs"
    : `${domain.label}${doy !== null ? ` — ${doyToLabel(doy)}` : ""}${season !== undefined ? ` ${season}` : ""}`;

  return (
    <div className="legend">
      <div className="legend-title">{title}</div>
      {showQuality ? (
        <div className="legend-quality">
          <div className="legend-quality-item">
            <span className="swatchdot" style={{ background: "var(--status-good)" }} />
            Parcel-level
          </div>
          <div className="legend-quality-item">
            <span className="swatchdot" style={{ background: "var(--status-warning)" }} />
            Aggregate-only
          </div>
        </div>
      ) : (
        <>
          <div className="legend-bar" />
          <div className="legend-scale">
            <span>{fmt(domain.min, 2)}</span>
            <span>{fmt(domain.max, 2)}</span>
          </div>
        </>
      )}
    </div>
  );
}
