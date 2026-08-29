import { useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import { DOMAINS, SEQ_RAMP_DARK, SEQ_RAMP_LIGHT, STATUS_GOOD, STATUS_WARNING, prefersDark } from "../domains";
import { fitBoundsView, geometryToFeature } from "../geo";
import type { Grain, IndexKey } from "../state/useFilters";
import type { MapItem } from "../types";

export interface ZoomHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  reset: () => void;
}

interface Props {
  items: MapItem[];
  index: IndexKey;
  grain: Grain;
  qualityOverlay: boolean;
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  centroid: [number, number] | null;
  zoomHandleRef: React.MutableRefObject<ZoomHandle | null>;
  onZoomLabel: (label: string) => void;
}

const DEFAULT_ZOOM = 10.5;
const FALLBACK_CENTER = { lon: -3.9, lat: 41.62 };

export default function PlotlyMapView({
  items,
  index,
  grain,
  qualityOverlay,
  selectedId,
  onSelect,
  centroid,
  zoomHandleRef,
  onZoomLabel,
}: Props) {
  const [isDark, setIsDark] = useState(prefersDark());
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setIsDark(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Parcel/H3-cell selections are tiny relative to the full DO extent — fit the view to
  // whatever's actually loaded instead of always centering on the DO-wide boundary, or a
  // single small municipio's geometry is sub-pixel at a fixed DO-scale zoom.
  const initialView = useMemo(() => {
    const fit = fitBoundsView(
      items.map((i) => i.boundary),
      { width: 900, height: 700 },
    );
    if (fit) return { center: { lon: fit.center[0], lat: fit.center[1] }, zoom: fit.zoom };
    return { center: centroid ? { lon: centroid[0], lat: centroid[1] } : FALLBACK_CENTER, zoom: DEFAULT_ZOOM };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, centroid?.[0], centroid?.[1]]);
  const [view, setView] = useState(initialView);
  useEffect(() => setView(initialView), [initialView]);

  useEffect(() => {
    zoomHandleRef.current = {
      zoomIn: () => setView((v) => ({ ...v, zoom: Math.min(v.zoom + 1, 18) })),
      zoomOut: () => setView((v) => ({ ...v, zoom: Math.max(v.zoom - 1, 3) })),
      reset: () => setView(initialView),
    };
  }, [zoomHandleRef, initialView]);

  useEffect(() => {
    onZoomLabel(`${view.zoom.toFixed(1)}×`);
  }, [view.zoom, onZoomLabel]);

  const geojson = useMemo(
    () => ({ type: "FeatureCollection" as const, features: items.map((i) => geometryToFeature(i.id, i.boundary)) }),
    [items],
  );
  const locations = useMemo(() => items.map((i) => i.id), [items]);

  const overlayActive = qualityOverlay && grain === "parcel";
  const domain = DOMAINS[index];
  const ramp = isDark ? SEQ_RAMP_DARK : SEQ_RAMP_LIGHT;

  const rawZ = useMemo(() => {
    if (overlayActive) return items.map((i) => (i.reliabilityClass === "parcel" ? 1 : 0));
    return items.map((i) => {
      const v = index === "ndvi" ? i.ndviMean : index === "ndre" ? i.ndreMean : i.ndmiMean;
      return v === null || v === undefined ? NaN : v;
    });
  }, [items, index, overlayActive]);

  // Plotly's choroplethmapbox skips NaN locations entirely — with no values loaded at all
  // (pipeline hasn't populated hex_obs/parcel_obs yet), fall back to a flat neutral fill so
  // boundaries stay visible instead of vanishing.
  const noValuesLoaded = !overlayActive && rawZ.every((v) => Number.isNaN(v));
  const z = noValuesLoaded ? items.map(() => 0) : rawZ;

  const colorscale = noValuesLoaded
    ? ([
        [0, isDark ? "#3a352b" : "#e4ddcf"],
        [1, isDark ? "#3a352b" : "#e4ddcf"],
      ] as [number, string][])
    : overlayActive
      ? ([
          [0, STATUS_WARNING],
          [1, STATUS_GOOD],
        ] as [number, string][])
      : ramp.map((c, i) => [i / (ramp.length - 1), c] as [number, string]);

  const markerOpacity = overlayActive
    ? items.map((i) => (i.pctValid !== null && i.pctValid !== undefined ? 0.35 + i.pctValid * 0.55 : 0.6))
    : 1;

  const lineColor = items.map((i) => {
    if (i.id === selectedId) return "#b8421a";
    if (grain === "parcel" && i.reliabilityClass === "aggregate_only") return "#756a59";
    return isDark ? "#14110c" : "#f3eee5";
  });
  const lineWidth = items.map((i) => (i.id === selectedId ? 2.2 : grain === "parcel" ? 0.6 : 0.5));

  const data: Data[] = [
    {
      type: "choroplethmapbox",
      geojson: geojson as unknown as object,
      locations,
      z,
      zmin: overlayActive || noValuesLoaded ? 0 : domain.min,
      zmax: overlayActive || noValuesLoaded ? 1 : domain.max,
      colorscale,
      showscale: false,
      marker: { opacity: markerOpacity, line: { color: lineColor, width: lineWidth } },
      hovertemplate: "%{location}<extra></extra>",
    } as unknown as Data,
  ];

  const layout: Partial<Layout> = {
    mapbox: {
      style: isDark ? "carto-darkmatter" : "carto-positron",
      center: view.center,
      zoom: view.zoom,
    } as Layout["mapbox"],
    margin: { t: 0, b: 0, l: 0, r: 0 },
    paper_bgcolor: "transparent",
    showlegend: false,
  };

  return (
    <Plot
      data={data}
      layout={layout}
      config={{ displayModeBar: false, scrollZoom: true }}
      style={{ width: "100%", height: "100%" }}
      useResizeHandler
      onRelayout={(e: Record<string, unknown>) => {
        const center = e["mapbox.center"] as { lon: number; lat: number } | undefined;
        const zoom = e["mapbox.zoom"] as number | undefined;
        if (center) setView((v) => ({ ...v, center }));
        if (zoom !== undefined) setView((v) => ({ ...v, zoom }));
      }}
      onClick={(e) => {
        const pt = e.points?.[0] as unknown as { location?: string } | undefined;
        if (pt?.location) onSelect(pt.location);
      }}
    />
  );
}
