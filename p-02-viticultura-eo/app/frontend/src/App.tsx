import { useEffect, useMemo } from "react";
import {
  useDates,
  useDoBoundary,
  useHexGeometry,
  useHexTimeseries,
  useHexValues,
  useMunicipios,
  useParcelGeometry,
  useParcelTimeseries,
  useParcelValues,
  useSeasons,
  useStatus,
} from "./api/hooks";
import type {
  HexCellTimeseriesPointOut,
  ParcelTimeseriesPointOut,
} from "./api/types";
import Inspector, { type SeriesPoint } from "./components/Inspector";
import MapPane from "./components/MapPane";
import Sidebar from "./components/Sidebar";
import TimeBar from "./components/TimeBar";
import TopBar from "./components/TopBar";
import StatStrip from "./components/StatStrip";
import { centroidOfGeometry, polygonAreaHa } from "./geo";
import { DEFAULT_RELIABILITY, useFilters, type Grain, type IndexKey, type ReliabilityClass } from "./state/useFilters";
import type { MapItem } from "./types";

function meanFor(index: IndexKey, ndvi: number | null, ndre: number | null, ndmi: number | null): number | null {
  return index === "ndvi" ? ndvi : index === "ndre" ? ndre : ndmi;
}

function extractHexSeries(index: IndexKey, points: HexCellTimeseriesPointOut[]): SeriesPoint[] {
  return points.map((p) => ({
    obsDate: p.obs_date,
    doy: p.doy,
    mean: meanFor(index, p.ndvi_mean, p.ndre_mean, p.ndmi_mean),
    p50: null,
    std: null,
  }));
}

function extractParcelSeries(index: IndexKey, points: ParcelTimeseriesPointOut[]): SeriesPoint[] {
  return points.map((p) => ({
    obsDate: p.obs_date,
    doy: p.doy,
    mean: index === "ndvi" ? p.ndvi_mean : index === "ndre" ? p.ndre_mean : p.ndmi_mean,
    p50: index === "ndvi" ? p.ndvi_p50 : index === "ndre" ? p.ndre_p50 : p.ndmi_p50,
    // parcel_obs has no ndmi_std column
    std: index === "ndvi" ? p.ndvi_std : index === "ndre" ? p.ndre_std : null,
  }));
}

export default function App() {
  const { filters, update } = useFilters();

  const seasonsQuery = useSeasons();
  const municipiosQuery = useMunicipios();
  const statusQuery = useStatus();
  const doBoundaryQuery = useDoBoundary();
  const datesQuery = useDates(filters.season, filters.grain);

  const hexGeometryQuery = useHexGeometry(filters.grain === "h3" ? filters.season : undefined, filters.municipioCodes);
  const hexValuesQuery = useHexValues(filters.grain === "h3" ? filters.season : undefined, filters.municipioCodes);
  const parcelGeometryQuery = useParcelGeometry(filters.grain === "parcel" ? filters.municipioCodes : []);
  const parcelValuesQuery = useParcelValues(filters.grain === "parcel" ? filters.season : undefined, filters.municipioCodes);

  const hexTimeseriesQuery = useHexTimeseries(
    filters.grain === "h3" ? filters.selectedId : undefined,
    filters.season,
  );
  const parcelTimeseriesQuery = useParcelTimeseries(
    filters.grain === "parcel" ? filters.selectedId : undefined,
    filters.season,
  );

  const seasons = seasonsQuery.data?.data ?? [];
  const municipios = municipiosQuery.data?.data ?? [];
  const dates = datesQuery.data?.data ?? [];

  // ---- bootstrap defaults: season, municipio, obs_date ----
  useEffect(() => {
    if (filters.season === undefined && seasons.length > 0) {
      const latest = Math.max(...seasons.map((s) => s.season));
      update({ season: String(latest) });
    }
  }, [filters.season, seasons, update]);

  useEffect(() => {
    if (filters.municipioCodes.length === 0 && municipios.length > 0) {
      update({ municipio_codes: municipios[0].codigo_municipio });
    }
  }, [filters.municipioCodes.length, municipios, update]);

  useEffect(() => {
    if (dates.length === 0) return;
    const stillValid = dates.some((d) => d.obs_date === filters.obsDate);
    if (!stillValid) {
      update({ obs_date: dates[dates.length - 1].obs_date });
    }
  }, [dates, filters.obsDate, update]);

  const centroid = useMemo(() => centroidOfGeometry(doBoundaryQuery.data?.boundary_geojson ?? null), [doBoundaryQuery.data]);

  const items: MapItem[] = useMemo(() => {
    if (filters.grain === "h3") {
      const geometryRows = hexGeometryQuery.data?.data ?? [];
      const valueRows = (hexValuesQuery.data?.data ?? []).filter((v) => v.obs_date === filters.obsDate);
      const valueById = new Map(valueRows.map((v) => [v.h3_cell_id, v]));
      return geometryRows.map((g) => {
        const v = valueById.get(g.h3_cell_id);
        const nPx = v ? (filters.index === "ndvi" ? v.n_px_ndvi : filters.index === "ndre" ? v.n_px_ndre : v.n_px_ndmi) : null;
        return {
          id: g.h3_cell_id,
          municipio: g.municipio,
          codigoMunicipio: g.codigo_municipio,
          boundary: g.boundary_geojson,
          areaHa: polygonAreaHa(g.boundary_geojson),
          reliabilityClass: null,
          ndviMean: v?.ndvi_mean ?? null,
          ndreMean: v?.ndre_mean ?? null,
          ndmiMean: v?.ndmi_mean ?? null,
          pctValid: null,
          nPx,
          hasValue: !!v,
        };
      });
    }

    const geometryRows = (parcelGeometryQuery.data?.data ?? []).filter((g) =>
      filters.reliability.includes(g.reliability_class as ReliabilityClass),
    );
    const valueRows = (parcelValuesQuery.data?.data ?? []).filter((v) => v.obs_date === filters.obsDate);
    const valueById = new Map(valueRows.map((v) => [v.recinto_id, v]));
    return geometryRows.map((g) => {
      const v = valueById.get(g.recinto_id);
      return {
        id: g.recinto_id,
        municipio: g.municipio,
        codigoMunicipio: g.codigo_municipio,
        boundary: g.boundary_geojson,
        areaHa: g.area_ha,
        reliabilityClass: g.reliability_class as ReliabilityClass,
        ndviMean: v?.ndvi_mean ?? null,
        ndreMean: v?.ndre_mean ?? null,
        ndmiMean: v?.ndmi_mean ?? null,
        pctValid: v?.pct_valid ?? null,
        nPx: v?.n_px_valid ?? null,
        hasValue: !!v,
      };
    });
  }, [
    filters.grain,
    filters.obsDate,
    filters.index,
    filters.reliability,
    hexGeometryQuery.data,
    hexValuesQuery.data,
    parcelGeometryQuery.data,
    parcelValuesQuery.data,
  ]);

  const geometryLoading =
    filters.grain === "h3"
      ? hexGeometryQuery.isLoading || hexGeometryQuery.isFetching
      : parcelGeometryQuery.isLoading || parcelGeometryQuery.isFetching;

  const dataAvailable = items.some((i) => i.hasValue);
  const selectedItem = items.find((i) => i.id === filters.selectedId);
  const currentDoy = dates.find((d) => d.obs_date === filters.obsDate)?.doy ?? null;

  const series: SeriesPoint[] = useMemo(() => {
    if (filters.grain === "h3") return extractHexSeries(filters.index, hexTimeseriesQuery.data?.data ?? []);
    return extractParcelSeries(filters.index, parcelTimeseriesQuery.data?.data ?? []);
  }, [filters.grain, filters.index, hexTimeseriesQuery.data, parcelTimeseriesQuery.data]);

  const timeseriesLoading = filters.grain === "h3" ? hexTimeseriesQuery.isLoading : parcelTimeseriesQuery.isLoading;

  const handleGrainChange = (g: Grain) => update({ grain: g, selected_id: null });
  const handleIndexChange = (k: IndexKey) => update({ index: k });
  const handleSeasonChange = (s: number) => update({ season: String(s), obs_date: null, selected_id: null });
  const handleToggleQuality = () => update({ quality_overlay: filters.qualityOverlay ? null : "1" });
  const handleToggleReliability = (r: ReliabilityClass) => {
    const next = filters.reliability.includes(r) ? filters.reliability.filter((x) => x !== r) : [...filters.reliability, r];
    update({ reliability: next.length === DEFAULT_RELIABILITY.length ? null : next.join(",") });
  };
  const handleToggleMunicipio = (codigo: string) => {
    const next = filters.municipioCodes.includes(codigo)
      ? filters.municipioCodes.filter((c) => c !== codigo)
      : [...filters.municipioCodes, codigo];
    update({ municipio_codes: next.join(","), selected_id: null });
  };
  const handleReset = () => update({ municipio_codes: null, quality_overlay: null, reliability: null, selected_id: null });
  const handleSelect = (id: string) => update({ selected_id: id });
  const handleCloseInspector = () => update({ selected_id: null });
  const handleDateChange = (date: string) => update({ obs_date: date });

  return (
    <div className="vit-root">
      <TopBar status={statusQuery.data} statusLoading={statusQuery.isLoading} />
      <StatStrip items={items} grain={filters.grain} index={filters.index} />
      <div className="body">
        <Sidebar
          filters={filters}
          seasons={seasons}
          municipios={municipios}
          onGrainChange={handleGrainChange}
          onIndexChange={handleIndexChange}
          onSeasonChange={handleSeasonChange}
          onToggleQuality={handleToggleQuality}
          onToggleReliability={handleToggleReliability}
          onToggleMunicipio={handleToggleMunicipio}
          onReset={handleReset}
        />
        <MapPane
          items={items}
          index={filters.index}
          grain={filters.grain}
          qualityOverlay={filters.qualityOverlay}
          selectedId={filters.selectedId}
          onSelect={handleSelect}
          centroid={centroid}
          doy={currentDoy}
          season={filters.season}
          dataAvailable={dataAvailable}
          municipioSelected={filters.municipioCodes.length > 0}
          awaitingSeason={filters.grain === "h3" && filters.season === undefined && filters.municipioCodes.length > 0}
          geometryLoading={geometryLoading}
        />
        <Inspector
          grain={filters.grain}
          index={filters.index}
          season={filters.season}
          obsDate={filters.obsDate}
          item={selectedItem}
          series={series}
          loading={timeseriesLoading}
          onClose={handleCloseInspector}
        />
      </div>
      <TimeBar dates={dates} obsDate={filters.obsDate} season={filters.season} onDateChange={handleDateChange} />
    </div>
  );
}
