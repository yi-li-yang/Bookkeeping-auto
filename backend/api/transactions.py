from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import Transaction
from schemas.transaction import TransactionOut, TransactionPatch

router = APIRouter(prefix="/api", tags=["transactions"])


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    start: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    if category:
        q = q.filter(Transaction.category == category)
    if search:
        q = q.filter(Transaction.description.ilike(f"%{search}%"))
    q = q.order_by(Transaction.date.desc())
    return q.offset(offset).limit(limit).all()


@router.patch("/transactions/{txn_id}", response_model=TransactionOut)
def update_category(
    txn_id: int,
    body: TransactionPatch,
    db: Session = Depends(get_db),
):
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    txn.category = body.category
    txn.is_user_edited = True
    db.commit()
    db.refresh(txn)
    return txn


@router.get("/categories", response_model=list[str])
def list_categories(db: Session = Depends(get_db)):
    rows = db.query(Transaction.category).distinct().order_by(Transaction.category).all()
    return [r[0] for r in rows if r[0]]
