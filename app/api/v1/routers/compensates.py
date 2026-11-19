"""账单赔付：页面只读库。"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import require_section
from app.db.session import get_db
from app.schemas.compensate import (
    CompensateDetail,
    CompensateShopList,
    CompensateShopOptions,
    CompensateShopRow,
)
from app.services import compensate_service

router = APIRouter(prefix="/compensates", tags=["账单赔付"])


@router.get("/shops", response_model=CompensateShopOptions, summary="店铺筛选")
def shops(db: Session = Depends(get_db), _user=Depends(require_section("fund"))) -> CompensateShopOptions:
    items = compensate_service.list_shop_options(db)
    return CompensateShopOptions.model_validate({"items": items})


@router.get("", response_model=CompensateShopList, summary="各店赔付合计")
def shop_totals(
    db: Session = Depends(get_db),
    _user=Depends(require_section("fund")),
    store_pk: int = Query(default=0),
    start_date: str = Query(default="", max_length=10),
    end_date: str = Query(default="", max_length=10),
) -> CompensateShopList:
    data = compensate_service.list_shop_totals(db, store_pk=store_pk, start_date=start_date, end_date=end_date)
    data["items"] = [CompensateShopRow.model_validate(row) for row in data["items"]]
    return CompensateShopList.model_validate(data)


@router.get("/export", summary="导出当前筛选的汇总和明细")
def export_file(
    db: Session = Depends(get_db),
    _user=Depends(require_section("fund")),
    store_pk: int = Query(default=0),
    start_date: str = Query(default="", max_length=10),
    end_date: str = Query(default="", max_length=10),
) -> Response:
    """两个工作表：汇总是各店合计，明细是这个时间里的每一条流水。"""
    content, filename = compensate_service.export_file(
        db, store_pk=store_pk, start_date=start_date, end_date=end_date
    )
    quoted = quote(filename)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"compensate.xlsx\"; filename*=UTF-8''{quoted}"},
    )


@router.get("/detail", response_model=CompensateDetail, summary="一家店的赔付流水")
def detail(
    db: Session = Depends(get_db),
    _user=Depends(require_section("fund")),
    store_pk: int = Query(...),
    start_date: str = Query(default="", max_length=10),
    end_date: str = Query(default="", max_length=10),
) -> CompensateDetail:
    data = compensate_service.list_detail(db, store_pk, start_date, end_date)
    return CompensateDetail.model_validate(data)
