import { DOMAINS, fmt } from "../domains";
import type { Grain, IndexKey } from "../state/useFilters";
import type { MapItem } from "../types";

interface Props {
  items: MapItem[];
  grain: Grain;
  index: IndexKey;
}

function valueFor(item: MapItem, index: IndexKey): number | null {
  return index === "ndvi" ? item.ndviMean : index === "ndre" ? item.ndreMean : item.ndmiMean;
}

export default function StatStrip({ items, grain, index }: Props) {
  const domain = DOMAINS[index];
  const withValue = items.filter((i) => i.hasValue);
  const mean = withValue.length
    ? withValue.reduce((s, i) => s + (valueFor(i, index) ?? 0), 0) / withValue.length
    : null;

  const haCovered = items.reduce((s, i) => s + (i.areaHa ?? 0), 0);
  const haLabel = haCovered >= 1000 ? `${(haCovered / 1000).toFixed(1)}k` : Math.round(haCovered).toLocaleString();

  const qualityCount = items.filter((i) => i.reliabilityClass === "parcel").length;
  const pxValues = items.map((i) => i.nPx).filter((v): v is number => v !== null);
  const meanPx = pxValues.length ? pxValues.reduce((s, v) => s + v, 0) / pxValues.length : null;

  const fourthTitle = grain === "parcel" ? "Parcel-level" : "Valid px / cell";
  const fourthLabel =
    grain === "parcel"
      ? `${items.length ? Math.round((qualityCount / items.length) * 100) : 0}%`
      : fmt(meanPx, 1);

  return (
    <div className="statstrip">
      <div className="stat-tile">
        <div className="stat-label">Area in view</div>
        <div className="stat-value">{haLabel} ha</div>
      </div>
      <div className="stat-tile">
        <div className="stat-label">Mean {domain.label}</div>
        <div className="stat-value">{fmt(mean, 2)}</div>
      </div>
      <div className="stat-tile">
        <div className="stat-label">{grain === "parcel" ? "Parcels shown" : "H3 cells shown"}</div>
        <div className="stat-value">{items.length.toLocaleString()}</div>
      </div>
      <div className="stat-tile">
        <div className="stat-label">{fourthTitle}</div>
        <div className="stat-value">{fourthLabel}</div>
      </div>
    </div>
  );
}
