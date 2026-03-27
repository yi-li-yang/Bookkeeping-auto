from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import CreditCard, Transaction

router = APIRouter(prefix="/api/credit-cards", tags=["credit-cards"])


class CreditCardIn(BaseModel):
    account_name: str
    card_name: Optional[str] = None
    credit_limit: Optional[float] = None
    promotion_end_date: Optional[str] = None  # YYYY-MM-DD
    fx_fee_pct: Optional[float] = None
    annual_fee: Optional[float] = None
    notes: Optional[str] = None


def _enrich(card: CreditCard, db: Session) -> dict:
    """Attach computed fields: current balance and usage pct from transactions."""
    balance = db.query(func.sum(Transaction.amount)).filter(
        Transaction.account_name == card.account_name
    ).scalar() or 0.0

    # For credit cards, balance is typically negative (you owe money).
    # We expose current_balance as a positive debt figure.
    current_balance = abs(round(balance, 2))
    usage_pct = round(current_balance / card.credit_limit * 100, 1) if card.credit_limit else None

    promo_days_left = None
    if card.promotion_end_date:
        from datetime import date
        try:
            promo_end = date.fromisoformat(card.promotion_end_date)
            promo_days_left = (promo_end - date.today()).days
        except ValueError:
            pass

    return {
        "id": card.id,
        "account_name": card.account_name,
        "card_name": card.card_name,
        "credit_limit": card.credit_limit,
        "current_balance": current_balance,
        "usage_pct": usage_pct,
        "promotion_end_date": card.promotion_end_date,
        "promo_days_left": promo_days_left,
        "fx_fee_pct": card.fx_fee_pct,
        "annual_fee": card.annual_fee,
        "notes": card.notes,
    }


@router.get("")
def list_credit_cards(db: Session = Depends(get_db)):
    """Return all stored credit card metadata enriched with live balance from transactions."""
    cards = db.query(CreditCard).order_by(CreditCard.card_name).all()
    return [_enrich(c, db) for c in cards]


@router.post("", status_code=201)
def create_credit_card(payload: CreditCardIn, db: Session = Depends(get_db)):
    existing = db.query(CreditCard).filter_by(account_name=payload.account_name).first()
    if existing:
        raise HTTPException(status_code=409, detail="A card with this account_name already exists.")
    card = CreditCard(**payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return _enrich(card, db)


@router.put("/{card_id}")
def update_credit_card(card_id: int, payload: CreditCardIn, db: Session = Depends(get_db)):
    card = db.query(CreditCard).filter_by(id=card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(card, k, v)
    db.commit()
    db.refresh(card)
    return _enrich(card, db)


@router.delete("/{card_id}", status_code=204)
def delete_credit_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(CreditCard).filter_by(id=card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")
    db.delete(card)
    db.commit()
