import type { Geometry } from "./api/types";

function ringAreaM2(ring: [number, number][], lat0: number): number {
  const mPerDegLat = 111320;
  const mPerDegLon = 111320 * Math.cos((lat0 * Math.PI) / 180);
  let sum = 0;
  for (let i = 0; i < ring.length; i++) {
    const [lon1, lat1] = ring[i];
    const [lon2, lat2] = ring[(i + 1) % ring.length];
    const x1 = lon1 * mPerDegLon;
    const y1 = lat1 * mPerDegLat;
    const x2 = lon2 * mPerDegLon;
    const y2 = lat2 * mPerDegLat;
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
}

/** Planar (equirectangular) approximation — fine for parcel/H3-cell-scale polygons in one narrow DO latitude band. */
export function polygonAreaHa(geometry: Geometry | undefined | null): number | null {
  if (!geometry) return null;
  let rings: number[][][] | null = null;
  if (geometry.type === "Polygon") rings = geometry.coordinates as number[][][];
  else if (geometry.type === "MultiPolygon") rings = (geometry.coordinates as number[][][][])[0];
  if (!rings || !rings[0] || rings[0].length < 3) return null;

  const outer = rings[0].map((p) => [p[0], p[1]] as [number, number]);
  const lat0 = outer.reduce((s, p) => s + p[1], 0) / outer.length;
  let areaM2 = ringAreaM2(outer, lat0);
  for (let i = 1; i < rings.length; i++) {
    areaM2 -= ringAreaM2(rings[i].map((p) => [p[0], p[1]] as [number, number]), lat0);
  }
  return areaM2 / 10000;
}

export function centroidOfGeometry(geometry: Geometry | undefined | null): [number, number] | null {
  if (!geometry) return null;
  let ring: number[][] | null = null;
  if (geometry.type === "Polygon") ring = (geometry.coordinates as number[][][])[0];
  else if (geometry.type === "MultiPolygon") ring = (geometry.coordinates as number[][][][])[0]?.[0] ?? null;
  if (!ring || !ring.length) return null;
  const lon = ring.reduce((s, p) => s + p[0], 0) / ring.length;
  const lat = ring.reduce((s, p) => s + p[1], 0) / ring.length;
  return [lon, lat];
}

export function geometryToFeature(id: string, geometry: Geometry) {
  return { type: "Feature" as const, id, geometry, properties: {} };
}

function extendBoundsFromRings(rings: number[][][], bounds: [number, number, number, number]) {
  for (const ring of rings) {
    for (const p of ring) {
      const [lon, lat] = p;
      if (lon < bounds[0]) bounds[0] = lon;
      if (lat < bounds[1]) bounds[1] = lat;
      if (lon > bounds[2]) bounds[2] = lon;
      if (lat > bounds[3]) bounds[3] = lat;
    }
  }
}

/**
 * Approximate a Plotly mapbox center/zoom that fits every item's boundary in view.
 * Parcel/H3-cell selections are tiny relative to the full DO extent — without this, a
 * single small municipio's geometry is sub-pixel at a DO-wide fixed zoom.
 */
export function fitBoundsView(
  boundaries: (Geometry | undefined | null)[],
  viewport: { width: number; height: number },
): { center: [number, number]; zoom: number } | null {
  const bounds: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const geometry of boundaries) {
    if (!geometry) continue;
    if (geometry.type === "Polygon") extendBoundsFromRings(geometry.coordinates as number[][][], bounds);
    else if (geometry.type === "MultiPolygon") {
      for (const poly of geometry.coordinates as number[][][][]) extendBoundsFromRings(poly, bounds);
    }
  }
  const [minLon, minLat, maxLon, maxLat] = bounds;
  if (!isFinite(minLon)) return null;

  const centerLon = (minLon + maxLon) / 2;
  const centerLat = (minLat + maxLat) / 2;
  const lonSpan = Math.max(maxLon - minLon, 0.001);
  const latSpan = Math.max(maxLat - minLat, 0.001);
  const pad = 1.35; // headroom so boundaries don't touch the viewport edge

  const WORLD_PX = 256;
  const zoomX = Math.log2((viewport.width / WORLD_PX) * (360 / (lonSpan * pad)));
  const latRad = (centerLat * Math.PI) / 180;
  const zoomY = Math.log2((viewport.height / WORLD_PX) * (360 / ((latSpan * pad) / Math.cos(latRad))));

  const zoom = Math.max(3, Math.min(17, Math.min(zoomX, zoomY)));
  return { center: [centerLon, centerLat], zoom };
}
