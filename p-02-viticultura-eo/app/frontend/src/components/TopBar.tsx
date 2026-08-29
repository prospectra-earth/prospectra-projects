import type { StatusOut } from "../api/types";

interface Props {
  status: StatusOut | undefined;
  statusLoading: boolean;
}

export default function TopBar({ status, statusLoading }: Props) {
  const label = statusLoading
    ? "Connecting…"
    : !status
      ? "Warehouse unreachable"
      : status.hex_obs_row_count === 0 && !status.parcel_obs_available
        ? "Live warehouse · pipeline not run yet"
        : `Live warehouse · ${status.hex_obs_row_count.toLocaleString()} hex obs`;

  const stale = statusLoading || !status?.warehouse_connected;

  return (
    <div className="topbar">
      <div className="brandmark">
        prospectra<span className="dot">·</span>
      </div>
      <div className="apptitle-wrap">
        <div className="apptitle">viticultura-eo</div>
        <div className="appsubtitle">DO Ribera del Duero · satellite vineyard intelligence</div>
      </div>
      <div className="topbar-right">
        <span className={`sample-pill${stale ? " stale" : ""}`}>
          <span className="dot-live" />
          {label}
        </span>
      </div>
    </div>
  );
}
