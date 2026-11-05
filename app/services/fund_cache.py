"""账户中心按整表放进 Redis。Redis 不可用时退回进程内缓存，页面仍然能打开。"""
from __future__ import annotations

import json
import threading
import time

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


def drop(kind: str) -> None:
    client = _client()
    if client is not None:
        try:
            client.delete(f"dy:fund:{kind}")
        except Exception:
            pass
    with _LOCK:
        _MEMORY.pop(kind, None)
