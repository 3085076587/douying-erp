"""把插件交来的一次登录原样入库。不写现有店铺凭证，也不去请求抖店。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.account_import import AccountImport
from app.schemas.account_import import AccountImportIn

_LOG = logging.getLogger("uvicorn.error")
_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "account_import.log"


def save_import(db: Session, body: AccountImportIn) -> AccountImport:
    names = sorted({str(item.get("name")) for item in body.cookies if item.get("name")})
    row = AccountImport(
        account=(body.account or "")[:100],
        shops_json=json.dumps(body.shops, ensure_ascii=False, separators=(",", ":")),
        cookies_json=json.dumps(body.cookies, ensure_ascii=False, separators=(",", ":")),
        local_storage_json=json.dumps(body.local_storage, ensure_ascii=False, separators=(",", ":")),
        ua=(body.ua or "")[:512],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"{when} import id={row.id} account={row.account or '-'} "
        f"shops={len(body.shops)} cookies={len(names)} names={','.join(names)}\n"
    )
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with _FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)
    _LOG.info("账号导入 id=%s shops=%s cookies=%s", row.id, len(body.shops), ",".join(names))
    return row
