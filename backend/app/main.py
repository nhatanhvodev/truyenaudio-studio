from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import create_health_router
from app.api.poc import create_poc_router
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
app.include_router(create_health_router())
app.include_router(create_poc_router())
