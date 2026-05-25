# P-01 — Where does Spain actually live?

Population density per H3 cell, cross-checked against NASA Black Marble night-lights.

- **Status:** in progress — ships May 26, 2026
- **Data:** INE Padrón Continuo (municipal) · Uber H3 res-9 · NASA Black Marble (VIIRS)
- **Stack:** Databricks · Apache Sedona · `h3_*` SQL functions · raster zonal stats

## Develop
Clone this repo into Databricks (Git folder) and build `notebook.ipynb` here.

## Publish
Export the notebook *with outputs* → copy into
`prospectra.earth/projects/p-01-spain-population/`, then push the website.
Published article: https://prospectra.earth/projects/p-01-spain-population.html
