import { DOMAINS } from "../domains";
import type { MunicipioOut, SeasonOut } from "../api/types";
import type { Filters, Grain, IndexKey, ReliabilityClass } from "../state/useFilters";

interface Props {
  filters: Filters;
  seasons: SeasonOut[];
  municipios: MunicipioOut[];
  onGrainChange: (g: Grain) => void;
  onIndexChange: (k: IndexKey) => void;
  onSeasonChange: (s: number) => void;
  onToggleQuality: () => void;
  onToggleReliability: (r: ReliabilityClass) => void;
  onToggleMunicipio: (codigo: string) => void;
  onReset: () => void;
}

const RELIABILITY_OPTIONS: { key: ReliabilityClass; label: string; color: string }[] = [
  { key: "parcel", label: "Parcel-level (reliable)", color: "var(--status-good)" },
  { key: "aggregate_only", label: "Aggregate-only", color: "var(--status-warning)" },
];

const CheckSvg = () => (
  <svg viewBox="0 0 16 16">
    <polyline points="3,8 6.5,12 13,4" />
  </svg>
);

export default function Sidebar({
  filters,
  seasons,
  municipios,
  onGrainChange,
  onIndexChange,
  onSeasonChange,
  onToggleQuality,
  onToggleReliability,
  onToggleMunicipio,
  onReset,
}: Props) {
  const seasonNumbers = seasons.map((s) => s.season);
  const municipioSet = new Set(filters.municipioCodes);

  return (
    <aside className="sidebar">
      <div className="filter-group">
        <div className="filter-group-head">
          <span className="filter-title">Grain</span>
        </div>
        <div className="seg">
          <button className={`seg-btn${filters.grain === "h3" ? " active" : ""}`} onClick={() => onGrainChange("h3")}>
            H3 cells
            <span className="sub">res 12</span>
          </button>
          <button
            className={`seg-btn${filters.grain === "parcel" ? " active" : ""}`}
            onClick={() => onGrainChange("parcel")}
          >
            Parcels
            <span className="sub">SIGPAC</span>
          </button>
        </div>
      </div>

      <div className="filter-group">
        <div className="filter-group-head">
          <span className="filter-title">Vegetation index</span>
        </div>
        <div className="seg">
          {(Object.keys(DOMAINS) as IndexKey[]).map((k) => (
            <button key={k} className={`seg-btn${filters.index === k ? " active" : ""}`} onClick={() => onIndexChange(k)}>
              {DOMAINS[k].label}
            </button>
          ))}
        </div>
        <div className="filter-hint">{DOMAINS[filters.index].hint}</div>
      </div>

      <div className="filter-group">
        <div className="filter-group-head">
          <span className="filter-title">Season</span>
        </div>
        <div className="chiprow">
          {seasonNumbers.map((s) => (
            <button key={s} className={`chip${filters.season === s ? " active" : ""}`} onClick={() => onSeasonChange(s)}>
              {s}
            </button>
          ))}
          {seasonNumbers.length === 0 && <span className="filter-hint">No seasons in hex_obs/parcel_obs yet.</span>}
        </div>
      </div>

      <div className="filter-group">
        <div className="filter-group-head">
          <span className="filter-title">Data quality</span>
        </div>
        <div className="switchrow">
          <span className="switchlabel">Shade by reliability</span>
          <div className={`switch${filters.qualityOverlay ? " on" : ""}`} onClick={onToggleQuality}>
            <div className="knob" />
          </div>
        </div>
        {filters.grain === "parcel" ? (
          <div style={{ marginTop: 8 }}>
            {RELIABILITY_OPTIONS.map((r) => {
              const on = filters.reliability.includes(r.key);
              return (
                <div key={r.key} className="checkrow" onClick={() => onToggleReliability(r.key)}>
                  <div className={`checkbox${on ? " on" : ""}`}>
                    <CheckSvg />
                  </div>
                  <span className="swatchdot" style={{ background: r.color }} />
                  <span className="checklabel">{r.label}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="filter-hint">
            reliability_class lives on parcel_obs only — hex_obs has no per-cell confidence field yet.
          </div>
        )}
      </div>

      <div className="filter-group">
        <div className="filter-group-head">
          <span className="filter-title">Municipality</span>
        </div>
        {municipios.map((m) => {
          const on = municipioSet.has(m.codigo_municipio);
          return (
            <div key={m.codigo_municipio} className="checkrow" onClick={() => onToggleMunicipio(m.codigo_municipio)}>
              <div className={`checkbox${on ? " on" : ""}`}>
                <CheckSvg />
              </div>
              <span className="checklabel">{m.municipio}</span>
            </div>
          );
        })}
      </div>

      <button className="reset-link" onClick={onReset}>
        Reset filters
      </button>
    </aside>
  );
}
