from typing import Optional
from pydantic import BaseModel


class TransactionOut(BaseModel):
    id: int
    date: str
    description: str
    amount: float
    category: Optional[str]
    account_name: Optional[str]
    account_type: Optional[str]
    source_file_id: int
    confidence: Optional[float]
    is_user_edited: bool

    model_config = {"from_attributes": True}


class TransactionPatch(BaseModel):
    category: str


class SourceFileOut(BaseModel):
    id: int
    filename: str
    file_type: str
    status: str
    row_count: int
    error_message: Optional[str]

    model_config = {"from_attributes": True}


class IngestResponse(BaseModel):
    processed: int
    skipped: int
    errors: int
    files: list[SourceFileOut]
