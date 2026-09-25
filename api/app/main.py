"""ERP API — placeholder entrypoint (replaced by the full app in plan-2)."""

from fastapi import FastAPI

app = FastAPI(title="ERP API", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
