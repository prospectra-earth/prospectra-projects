# Prospectra Projects

Development workspace for Prospectra's geospatial portfolio projects (P-01 … P-05).
This is where the **analysis runs** — this repo is cloned into Databricks as a Git folder,
and the notebooks are developed and executed there. The polished result is published
separately on the website.

## The three repos

| Repo | Role | Visibility |
|---|---|---|
| `franjmartin21/ProspectraGeospatial` | **Brain** — curriculum, lessons, plans (private lab notebook) | private |
| `prospectra-earth/prospectra-projects` *(this one)* | **Dev** — runnable analysis notebooks, cloned in Databricks | public |
| `ceo-prospectra/prospectra.earth` | **Website** — the published site at https://prospectra.earth | public |

## Workflow: develop here → publish to the website

1. **Develop & run** the notebook in Databricks (this repo cloned as a Git folder).
2. **Export with outputs** — run all cells, then *File ▸ Export ▸ IPython Notebook (.ipynb)*.
   Use **matplotlib / plotly / folium** for figures (they embed into the `.ipynb`;
   Databricks `display()` widgets often don't serialize).
3. **Place in the website** — drop the exported `.ipynb` + figures into
   `prospectra.earth/projects/p-0X-slug/`, write/update its `index.qmd`, and push.
   The website renders the **saved** outputs (`execute: enabled: false`) — it never
   re-runs Spark at build time.

## Layout

```
p-0X-slug/
├── README.md       ← the question, data sources, status
├── notebook.ipynb  ← the analysis (developed + run in Databricks)
└── figures/        ← exported maps / plots
```

## Data

Raw datasets (INE dumps, rasters, parquet) are **not committed** — see `.gitignore`.
Keep them in Databricks volumes or a local `data/` dir; each notebook documents how to
fetch them. Commit only code, small result tables, and exported figures.
