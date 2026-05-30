# P-01 — "Where does Spain actually live?"
### Build plan (Phase A: administrative + H3 view)

> **What this is:** an end-to-end design for what P-01 Phase A produces. Bolo implements it in
> Databricks. This document is the *what*, not the *how* — no notebook cell scripting.
> **Status:** Phase A approved May 30, 2026.

---

## 1. Why this project, why two phases

P-01 is the first portfolio project and the opener of the **P-01 → P-03 demographic trilogy**
(population → migration → housing). Its curriculum hook is *population density via H3 with a
night-lights raster validator*: INE Padrón population + IGN municipal boundaries + NASA Black
Marble VIIRS night lights.

The night-lights validator needs skills from **L-08 (rasterio / zonal stats)** and
**L-10 (STAC / COG)**, which are still ahead on the curriculum. So P-01 ships in two phases:

- **Phase A (this plan) — administrative + H3 view.** Consolidates L-01 → L-07: CRS discipline,
  vector data + GeoParquet, H3 indexing, GeoPandas, spatial joins / dissolve. Honest, but
  explicitly admits its limit: density per polygon *smears people uniformly across the whole
  municipality*, including uninhabited mountains and fields.
- **Phase B (later, after L-08 + L-10) — night-lights reality check.** Black Marble VIIRS as
  both a **validator** ("the census says N people here; the lights say they're all in one
  corner") and a **dasymetric weight** that reallocates census population to H3 cells by
  brightness. This is the original, publishable twist.

**Publish only after Phase B.** Phase A alone is "another Spain choropleth"; the night-lights
layer is what makes the story worth telling.

### Narrative spine
> *Administrative geography lies about where people live.*
> naive **count** map → **density** map → **H3** map — each less wrong than the last —
> with night lights (Phase B) as the final ground truth.

---

## 2. Locked decisions

| Decision | Choice |
|---|---|
| Scope of this phase | Phase A now; Phase B deferred to post L-08/L-10 |
| Build environment | Databricks Free Edition serverless (Bolo implements) |
| Geographic coverage | All of Spain incl. Canarias / Ceuta / Melilla; Canarias drawn as an inset |
| Validation approach | Prove the pipeline on one autonomous community, then run national |

---

## 3. Data sources

| Source | What it provides | Join key |
|---|---|---|
| **INE Padrón** — "Cifras oficiales de población de los municipios" (latest, 2024), Tempus3 JSON API or CSV | Population per municipality | 5-digit **código INE** |
| **IGN CNIG "Unidades administrativas" (SIANE) GeoPackage** | Municipio / provincia / CCAA polygons | código INE on each layer |
| **NASA Black Marble VIIRS (VNP46A)** — *Phase B only, not used now* | Annual night-lights raster | — |

---

## 4. What we build (end-to-end)

The pipeline turns two raw sources into four analysis-ready layers and a set of maps.

### 4.1 Population pipeline → GeoParquet layers
1. **Ingest** INE Padrón and the IGN GeoPackage into a Unity Catalog Volume.
2. **Join** population to municipal polygons on a normalized 5-char `cod_ine` (attribute join,
   not a spatial join). Every municipality must match — zero unmatched codes.
3. **Density** — reproject to **EPSG:3035** (equal-area), compute `area_km2`, and
   `density = poblacion / area_km2`. (Area math never in degrees — keep WGS84 4326 only for
   display.)
4. **Aggregate** — `dissolve` the municipal layer up to **province** and **comunidad autónoma**,
   re-summing population and recomputing density.
5. **H3 allocation** — polyfill municipalities to **H3 res-9** and area-weight each
   municipality's population across the cells it covers. Per-CCAA population must be conserved
   (cell sums equal the CCAA total within rounding tolerance).
6. **H3 roll-up for visualization** — aggregate res-9 cells to **res-6/7 parents** for national
   maps (res-9 is ~4.8M cells nationally, too heavy to render; the hierarchy roll-up reuses
   L-05 H3 fluency).

**Outputs:** four GeoParquet layers — municipal, province, CCAA, and H3 (res-9 + viz roll-up).

### 4.2 Maps
- **Choropleth classification** — density is heavily right-skewed (Madrid/BCN vs empty rural),
  so linear bins produce a single-colour map. Compare quantiles / Fisher-Jenks / log
  (`mapclassify`) and pick + justify a scheme.
- **Interactive folium** — layered map with `LayerControl` toggling municipality / province /
  CCAA / H3, and separate **count vs density** views. Municipal geometries simplified so the
  ~8,131 polygons stay light in the browser; Canarias translated into an inset box.
- **Static PNGs** — high-resolution exports for the X thread (insets trivial in matplotlib).

---

## 5. Deliverables

- Four GeoParquet layers (municipal / province / CCAA / H3).
- Interactive folium HTML map(s) with toggleable admin levels + H3 and count/density views.
- Static PNG maps for publication, saved to this project's `figures/` directory.
- Runnable Databricks notebook in this project folder; results exported into
  `prospectra.earth/projects/p-01-spain-population/`.

---

## 6. Verification

- Join: zero unmatched `cod_ine`.
- Aggregation: province + CCAA population totals equal the sum of their municipalities.
- H3: cell population total ≈ municipal total (conservation); per-CCAA within tolerance.
- National: sum of municipal population ≈ 48M.
- Maps: folium layers toggle and the Canarias inset renders; PNGs read clearly at thread size.

---

## 7. Out of scope for Phase A (deferred)

- Night-lights raster ingest, zonal stats, dasymetric reallocation → **Phase B** (post L-08/L-10).
- Sedona / Mosaic distributed "at scale" version → natural **Block-3** follow-up.
- Publishing the thread / article → only after Phase B completes the story.

---

## 8. Further reading

*Geographic Data Science with Python* (Rey et al.) — **Choropleth Mapping** chapter
(classification schemes); **Spatial Autocorrelation** chapters for later defending any
clustering claims in the published thread.
