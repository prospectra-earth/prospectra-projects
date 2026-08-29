import { Suspense, lazy, useCallback, useRef, useState } from "react";
import type { Grain, IndexKey } from "../state/useFilters";
import type { MapItem } from "../types";
import Legend from "./Legend";
import type { ZoomHandle } from "./PlotlyMapView";

const PlotlyMapView = lazy(() => import("./PlotlyMapView"));

interface Props {
  items: MapItem[];
  index: IndexKey;
  grain: Grain;
  qualityOverlay: boolean;
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  centroid: [number, number] | null;
  doy: number | null;
  season: number | undefined;
  dataAvailable: boolean;
  municipioSelected: boolean;
  awaitingSeason: boolean;
  geometryLoading: boolean;
}

export default function MapPane({
  items,
  index,
  grain,
  qualityOverlay,
  selectedId,
  onSelect,
  centroid,
  doy,
  season,
  dataAvailable,
  municipioSelected,
  awaitingSeason,
  geometryLoading,
}: Props) {
  const zoomHandleRef = useRef<ZoomHandle | null>(null);
  const [zoomLabel, setZoomLabel] = useState("1.0×");
  const onZoomLabel = useCallback((label: string) => setZoomLabel(label), []);

  const grainNoun = grain === "parcel" ? "parcels" : "cells";

  return (
    <main className="mapwrap">
      <div className="mapcard">
        {items.length > 0 && (
          <Suspense fallback={null}>
            <PlotlyMapView
              items={items}
              index={index}
              grain={grain}
              qualityOverlay={qualityOverlay}
              selectedId={selectedId}
              onSelect={onSelect}
              centroid={centroid}
              zoomHandleRef={zoomHandleRef}
              onZoomLabel={onZoomLabel}
            />
          </Suspense>
        )}

        {items.length > 0 && !dataAvailable && (
          <div className="map-empty-state">
            <div className="headline">No {index.toUpperCase()} values loaded for this date yet</div>
            <div className="sub">
              {grain === "h3"
                ? "hex_obs has no rows for this season/date — showing cell boundaries only."
                : "parcel_obs isn't populated yet — showing parcel boundaries only."}
            </div>
          </div>
        )}

        {items.length === 0 && !municipioSelected && (
          <div className="map-empty-state">
            <div className="headline">No geometry for this selection</div>
            <div className="sub">Pick at least one municipality in the sidebar.</div>
          </div>
        )}

        {items.length === 0 && municipioSelected && !geometryLoading && awaitingSeason && (
          <div className="map-empty-state">
            <div className="headline">No H3 seasons processed yet</div>
            <div className="sub">
              hex_obs has no rows yet, so no season is available at this grain — the pipeline hasn't caught up.
              Switch to Parcel grain to see the SIGPAC boundaries already loaded.
            </div>
          </div>
        )}

        {items.length === 0 && municipioSelected && geometryLoading && (
          <div className="map-empty-state">
            <div className="headline">Loading {grain === "parcel" ? "parcels" : "cells"}…</div>
            <div className="sub">Fetching geometry for the selected municipalities.</div>
          </div>
        )}

        {items.length === 0 && municipioSelected && !geometryLoading && !awaitingSeason && (
          <div className="map-empty-state">
            <div className="headline">No {grain === "parcel" ? "parcels" : "cells"} found</div>
            <div className="sub">This municipality selection returned no {grain === "parcel" ? "parcel" : "H3 cell"} geometry.</div>
          </div>
        )}

        <div className="mapmeta">
          <span className="mapmeta-pill">
            {items.length.toLocaleString()} {grainNoun} shown
          </span>
        </div>

        <Legend index={index} grain={grain} qualityOverlay={qualityOverlay} doy={doy} season={season} />

        <div className="zoomctrl">
          <button className="zoombtn" onClick={() => zoomHandleRef.current?.zoomIn()} title="Zoom in">
            +
          </button>
          <div className="zoomlabel">{zoomLabel}</div>
          <button className="zoombtn" onClick={() => zoomHandleRef.current?.zoomOut()} title="Zoom out">
            −
          </button>
          <button className="zoombtn reset" onClick={() => zoomHandleRef.current?.reset()} title="Reset view">
            ↺
          </button>
        </div>
      </div>
    </main>
  );
}
