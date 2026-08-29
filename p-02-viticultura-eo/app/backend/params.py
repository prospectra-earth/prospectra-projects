from fastapi import HTTPException, Query


def municipio_codes_param(
    municipio_codes: str = Query(..., description="Comma-separated codigo_municipio list, e.g. '09018,09999'"),
) -> list[str]:
    codes = [c for c in municipio_codes.split(",") if c.strip()]
    if not codes:
        raise HTTPException(status_code=422, detail="municipio_codes must contain at least one code")
    return codes
