from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.settings.config import Settings
from app.settings.startup_lock import StartupLock


settings = Settings()
api_lock = StartupLock(settings.data_root / "studio-api.lock")


@asynccontextmanager
async def lifespan(app: FastAPI):
    api_lock.acquire()
    try:
        yield
    finally:
        api_lock.release()


app = FastAPI(lifespan=lifespan)


@app.get("/api/health/live")
def health_live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/api/health/ready")
def health_ready() -> dict[str, str]:
    return {"status": "ready"}
