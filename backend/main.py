import logging
import os
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


def _background_ingest():
    from db.database import SessionLocal
    from ingestion.file_scanner import scan_and_ingest
    db = SessionLocal()
    try:
        logger.info("Background ingest started…")
        result = scan_and_ingest(db)
        logger.info(f"Background ingest done: {result['processed']} processed, {result['skipped']} skipped, {result['errors']} errors")
    except Exception as exc:
        logger.error(f"Background ingest failed: {exc}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from db.database import init_db
    init_db()
    threading.Thread(target=_background_ingest, daemon=True).start()
    yield


app = FastAPI(title="Bookkeeping Auto API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from api import ingestion, transactions, analytics, investments  # noqa: E402

app.include_router(ingestion.router)
app.include_router(transactions.router)
app.include_router(analytics.router)
app.include_router(investments.router)


@app.get("/health")
def health():
    return {"status": "ok"}
