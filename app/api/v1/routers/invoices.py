"""发票管理：我给平台开票、给消费者开票的数量。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import require_section
from app.db.session import get_db
from app.schemas.invoice import InvoiceList, InvoiceProgress, InvoiceRow
from app.services import invoice_service

router = APIRouter(prefix="/invoices", tags=["发票管理"])


@router.get("", response_model=InvoiceList, summary="发票数量")
def list_invoices(db: Session = Depends(get_db), _user=Depends(require_section("fund"))) -> InvoiceList:
    payload = invoice_service.list_invoices(db)
    payload["items"] = [InvoiceRow.model_validate(row) for row in payload["items"]]
    return InvoiceList.model_validate(payload)


@router.get("/progress", response_model=InvoiceProgress, summary="发票拉取进度")
def get_progress(_user=Depends(require_section("fund"))) -> InvoiceProgress:
    return InvoiceProgress.model_validate(invoice_service.progress())


@router.post("/refresh", response_model=InvoiceProgress, summary="拉取发票数量")
def refresh(_user=Depends(require_section("fund"))) -> InvoiceProgress:
    return InvoiceProgress.model_validate(invoice_service.start_refresh())
