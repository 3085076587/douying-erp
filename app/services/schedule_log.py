"""定时拉取的明文记录。启动、成功、失败都写到 logs/schedule.log。"""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "schedule.log"


def _append(text: str) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with _FILE.open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def _stamp(kind: str, name: str) -> None:
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _append(f"{when} {kind} {name}\n")


def write_start(name: str) -> None:
    _stamp("start", name)


def write_success(name: str) -> None:
    _stamp("success", name)


def write_fail(name: str, detail: str | None = None) -> None:
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = (detail if detail is not None else traceback.format_exc()).rstrip()
    _append(f"{when} fail {name}\n{body}\n")
