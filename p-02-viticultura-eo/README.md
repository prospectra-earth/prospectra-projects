# viticultura-eo

Satellite vineyard intelligence for **DO Ribera del Duero**. Sentinel-2 → H3 res-12 → multi-year
NDVI/NDRE/NDMI, served as an interactive map with a yield-forecast harness attached.

Project P-02 of the Prospectra portfolio. Spec: `ribera-eo-mvp-spec.md`.

---

## Notebooks

Every notebook starts with `%run ./00_setup`, which installs dependencies and defines shared
constants and helpers. Nothing else installs anything.

| Notebook | Cadence | In | Out |
|---|---|---|---|
| `00_setup` | — | — | constants + helpers, writes nothing |
| `01_reference_geometry` | **manual, once per DO** | MAPA boundary zip, SIGPAC zips, `municipios_geo_raw` | `ref_do_boundary`, `ref_municipios`, `ref_search_envelope`, `ref_vineyard_parcels`, `ref_uso_audit` |
| `02_ingest_imagery` | **recurring** | `ref_search_envelope`, Earth Search STAC | `stac_items` |
| `03_vineyard_mask` | manual | `ref_vineyard_parcels` | `vineyard_mask` — *not built yet* |

The split is deliberate: **reference geometry is near-static** (new plantings authorised in Ribera
for 2026 totalled 0.1 ha) while imagery arrives every ~2.5 days. NB01 loads **one SIGPAC snapshot**
and is re-run only for a new vintage, a boundary revision, or a new DO.

---

## Tables — measured, all live

| Table | Rows | Notes |
|---|---|---|
| `ref_do_boundary` | **1** | DO polygon, `ZON_CO_ID = 49`, 3,243 km² |
| `ref_municipios` | **82** | Geometry, `frac_inside`, INE↔cadastral crosswalk, download URLs |
| `ref_search_envelope` | **1** | `[-4.4569, 41.3860, -2.8697, 41.8999]` |
| `ref_vineyard_parcels` | **69,785** | 30,721 ha of `uso='VI'`, clipped to the DO |
| `ref_uso_audit` | small | Area by land-use code |
| `stac_items` | **9,516** | 1,586 items × 6 bands, 4 seasons, **6 tiles** |

Intermediates are temp views (`v_boundary`, `v_municipios`, `v_xwalk`, `v_recintos`, `v_parcels`) —
nothing staging-related is persisted.

---

## Running it

**1. Boundary** — download `calidaddiferenciada_vinos.zip` from
[MAPA](https://www.mapa.gob.es/es/cartografia-y-sig/ide/descargas/alimentacion/vinos) into
`/Volumes/geospatial/ribera_duero/raw/`. The published URL serves an HTML page, so this is manual.

**2. NB01 Phase 1** — run `01_reference_geometry`. It completes the boundary, municipality and
envelope tables, writes the SIGPAC download list, and stops cleanly at the Phase 2 gate.

**3. Parcels** — 82 files, ~453 MB, ~7 minutes:

```bash
./scripts/fetch_sigpac.sh          # defaults to 2025
```

Skips what is already uploaded, so it is safe to re-run after an interruption.

**4. NB01 Phase 2** — re-run the notebook. Then `02_ingest_imagery`.

---

## Conventions

**CRS** — store **4258**, measure **25830**. All three vector sources are 4258, so they join without
reprojection; only measurement needs transforming. `ST_Area` on a 4258 geometry returns square
degrees.

**Identity** — `recinto_id` is SIGPAC's `C_REFREC` (23 chars). `codigo_municipio` is INE;
`codigo_catastral` is what appears in `C_REFREC`. They are **not** interchangeable.

**Area** — `SUPERFICIE` (m², authoritative) or computed in 25830. Never `Shape_Area`, which is in
square degrees.

**Collection** — `sentinel-2-l2a`, not `c1-l2a`: Collection 1 returns zero items over Ribera for 2022.

**Radiometry** — `reflectance = DN × scale + offset`, read per item per band from `raster:bands`.
Never inferred from date or baseline: ESA reprocessed parts of the archive, so a 2019 scene carries
baseline 05.00 and needs the offset while a 2021 scene carries 03.01 and must not have it.

---

## Findings worth knowing

**INE ≠ cadastral municipality codes.** ITACyL names SIGPAC files by cadastral code;
`municipios_geo_raw` uses INE. **78 of 82 differ**, with cumulative drift (Roa 09321→09332,
San Esteban de Gormaz 42162→42263), and they collide — cadastral `09155` is *Gumiel de Mercado*,
INE `09155` is *Haza*. Match on **normalised name**: 82/82, zero collisions. Code matching produced
three confirmed wrong-municipality loads before the 1:1 assertion caught it.

**The search bbox was wrong.** The old hardcoded `[-4.5, 41.4, -3.2, 41.9]` stopped at longitude
−3.2 while the DO reaches −2.894 — about 20% of the denomination, the Soria end, was never searched.
Deriving it from the boundary took `stac_items` from 4 tiles to **6** (adding 30TWL, 30TWM).

**ITACyL quirks.** Folder naming differs by year (`Burgos/` vs `09_Burgos/`); `ñ` is served as
`%c2%b1` (U+00B1 `±`); 2024 ships only 124 of 225 Valladolid municipalities — use 2022, 2023 or 2025.

**UC Volumes are not under `/dbfs/`.** `dbutils.fs.ls` returns `dbfs:/Volumes/...`; rewriting that
to `/dbfs/Volumes/...` makes every read fail.

---

## Open items

- [ ] **SIGPAC licence.** ITACyL, every year 2012–2024: *"Está permitido el uso gratuito de los
      datos, pero queda prohibida su explotación comercial."* Free use yes, **commercial exploitation
      no**. Spec §2's premise that v0.1 is commercially usable because it is public is not currently
      true, and the parcel mask is the foundational layer. The only open item that could invalidate
      the project rather than delay it.

- [ ] **Boundary covers 82 of 102 municipalities.** The MAPA layer is a 2014 vintage. Valladolid
      (19) and Segovia (4) match exactly; Burgos is short 7 and **Soria short 13**. Since
      `frac_inside` is 1.0 everywhere — the boundary is a municipal dissolve — the fix is to take
      the current member list from the BOCyL *pliego* and define the DO as their union.

- [ ] **Spec D5 does not survive the data.** Measured on the 2025 load:

      | stage | parcels | ha | % area kept |
      |---|---|---|---|
      | raw VI in DO | 69,785 | 30,721 | 100% |
      | after −15 m buffer | 24,333 | 13,352 | 43% |
      | + 0.5 ha floor | **5,838** | **11,196** | **36%** |

      It discards **92% of parcels and 64% of area**, because the median parcel is **0.119 ha**
      (~34 × 34 m) and a −15 m inward buffer erases it. Options: shrink the buffer to −5/−10 m (one
      Sentinel-2 pixel), drop the area floor and rely on H3 `coverage_fraction` weighting, or accept
      ~36% coverage and say so plainly.

- [ ] Spec **D4** (fixed mask across years) deferred — one snapshot is enough while the register is
      frozen. Load a second `sigpac_year` if phenology later shows cadastre-driven step changes.
- [ ] Scrape *Superficie de Viñedo por Municipio* for QA gate S2.
- [ ] Does the Consejo publish production **by municipality**? Spec §16's highest-leverage unknown —
      it would take the model from n=7 to ~800.
