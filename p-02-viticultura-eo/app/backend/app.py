from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .data_access.repository import TableNotReadyError
from .routers import hex_cells, meta, parcels

app = FastAPI(title="viticultura-eo")

app.include_router(meta.router)
app.include_router(hex_cells.router)
app.include_router(parcels.router)


@app.exception_handler(TableNotReadyError)
def table_not_ready_handler(request: Request, exc: TableNotReadyError):
    return JSONResponse(
        status_code=503,
        content={"error": "table_not_ready", "detail": str(exc)},
    )


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        return FileResponse(FRONTEND_DIST / "index.html")
