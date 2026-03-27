import logging
import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import InvestmentHolding, SourceFile, Transaction
from ingestion.file_scanner import scan_and_ingest, INPUT_DIR, SUPPORTED_EXTENSIONS
from schemas.transaction import IngestResponse, SourceFileOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.post("/ingest", response_model=IngestResponse)
def trigger_ingest(force: bool = False, db: Session = Depends(get_db)):
    if force:
        _clear_processed_files(db)

    scan_result = scan_and_ingest(db)

    return IngestResponse(
        processed=scan_result["processed"],
        skipped=scan_result["skipped"],
        errors=scan_result["errors"],
        files=[SourceFileOut.model_validate(f) for f in scan_result["files"]],
    )


def _clear_processed_files(db: Session) -> None:
    """Delete DB records for all files currently in INPUT_DIR so they get reprocessed."""
    for root, _, filenames in os.walk(INPUT_DIR):
        for fname in sorted(filenames):
            if os.path.splitext(fname)[1].lower() not in SUPPORTED_EXTENSIONS:
                continue
            rel_name = os.path.relpath(os.path.join(root, fname), INPUT_DIR)
            sf = db.query(SourceFile).filter_by(filename=rel_name).first()
            if sf:
                db.query(Transaction).filter_by(source_file_id=sf.id).delete()
                db.query(InvestmentHolding).filter_by(source_file_id=sf.id).delete()
                db.delete(sf)
    db.commit()
    logger.info("Force reingest: cleared all processed file records")
