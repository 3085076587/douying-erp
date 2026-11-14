"""账户中心按整表放进 Redis。Redis 不可用时退回进程内缓存，页面仍然能打开。"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

_LOCK = threading.Lock()
_MEMORY: dict[str, tuple[float, str]] = {}
_redis = None
_redis_failed = False


def _client():
    global _redis, _redis_failed
    if _redis_failed:
        return None
    if _redis is not None:
        return _redis
    try:
        import redis

        from app.core.config import get_settings

        client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        client.ping()
        _redis = client
        return client
    except Exception:
        _redis_failed = True
        return None


def _raw(kind: str) -> str | None:
    raw = None
    client = _client()
    if client is not None:
        try:
            raw = client.get(f"dy:fund:{kind}")
        except Exception:
            raw = None
    if raw is None:
        with _LOCK:
            slot = _MEMORY.get(kind)
        if slot is None or slot[0] < time.time():
            return None
        raw = slot[1]
    return raw


def _save(kind: str, raw: str, ttl_seconds: int) -> None:
    client = _client()
    if client is not None:
        try:
            client.set(f"dy:fund:{kind}", raw, ex=ttl_seconds)
        except Exception:
            pass
    with _LOCK:
        _MEMORY[kind] = (time.time() + ttl_seconds, raw)


def read(kind: str) -> list | None:
    raw = _raw(kind)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def read_obj(kind: str) -> dict | None:
    raw = _raw(kind)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def write(kind: str, rows: list, ttl_seconds: int) -> None:
    _save(kind, json.dumps(rows, ensure_ascii=False, default=str), ttl_seconds)


def write_obj(kind: str, payload: dict, ttl_seconds: int) -> None:
    _save(kind, json.dumps(payload, ensure_ascii=False, default=str), ttl_seconds)


def kinds() -> list[str]:
    found: set[str] = set()
    client = _client()
    if client is not None:
        try:
            for key in client.scan_iter("dy:fund:*"):
                found.add(str(key)[len("dy:fund:"):])
        except Exception:
            pass
    with _LOCK:
        found.update(_MEMORY)
    return list(found)


def _ttl(kind: str) -> int:
    client = _client()
    if client is not None:
        try:
            left = int(client.ttl(f"dy:fund:{kind}"))
        except Exception:
            left = -1
        if left > 0:
            return left
    with _LOCK:
        slot = _MEMORY.get(kind)
    if slot is None:
        return 7 * 24 * 3600
    return max(1, int(slot[0] - time.time()))


def _parsed(kind: str):
    raw = _raw(kind)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _row_pk(row: object) -> int:
    if not isinstance(row, dict) or "store_pk" not in row:
        return 0
    try:
        return int(row.get("store_pk") or 0)
    except (TypeError, ValueError):
        return 0


def scrub_store(store_pk: int) -> None:
    """从整表缓存里拿掉这一家。店铺明细、首页、发票和账户快照都在这里。"""
    if store_pk <= 0:
        return
    for kind in kinds():
        if kind == f"dy:violation:{store_pk}":
            drop(kind)
            continue
        data = _parsed(kind)
        if isinstance(data, dict) and kind == "live" and str(store_pk) in data:
            data.pop(str(store_pk), None)
            write_obj(kind, data, _ttl(kind))
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            kept = [row for row in data["items"] if _row_pk(row) != store_pk]
            if len(kept) != len(data["items"]):
                data["items"] = kept
                write_obj(kind, data, _ttl(kind))
            continue
        if isinstance(data, list):
            kept = [row for row in data if _row_pk(row) != store_pk]
            if len(kept) != len(data):
                write(kind, kept, _ttl(kind))


def drop(kind: str) -> None:
    client = _client()
    if client is not None:
        try:
            client.delete(f"dy:fund:{kind}")
        except Exception:
            pass
    with _LOCK:
        _MEMORY.pop(kind, None)


def stamp_fresh(value, seconds: int = 600) -> bool:
    """时间戳距现在不到 seconds 秒。认 Redis 里写过的两种格式。"""
    text = str(value or "").strip().replace("T", " ")[:19]
    if len(text) < 19:
        return False
    try:
        updated = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return (datetime.now() - updated).total_seconds() < seconds
