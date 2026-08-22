import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import analytics, credit_cards, ingestion, investments, transactions
from backend.config import settings
from backend.db.database import init_db

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Bookkeeping Auto API", lifespan=lifespan)

# Prod serves the SPA same-origin and dev proxies /api through Vite, so this
# only needs to cover the dev server's origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion.router)
app.include_router(transactions.router)
app.include_router(analytics.router)
app.include_router(investments.router)
app.include_router(credit_cards.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Mounted last: Starlette matches routes in registration order, so every /api/*
# route, /health and /docs still win over the catch-all static mount.
if settings.frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="ui")
else:
    logger.warning(
        "frontend/dist not found at %s — run `npm run build`. Serving API only.",
        settings.frontend_dist,
    )
