from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.database import get_db
from ingestion.file_scanner import scan_and_ingest
from schemas.transaction import IngestResponse, SourceFileOut

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.post("/ingest", response_model=IngestResponse)
def trigger_ingest(db: Session = Depends(get_db)):
    result = scan_and_ingest(db)
    return IngestResponse(
        processed=result["processed"],
        skipped=result["skipped"],
        errors=result["errors"],
        files=[SourceFileOut.model_validate(f) for f in result["files"]],
    )
