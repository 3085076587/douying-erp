"""抖店、罗盘的请求节奏。

同一家店的请求严格串行。不同店同时最多 4 家，全局每秒最多放行 6 次。
店铺首页单独放宽到 12 家同时拉。罗盘和别的接口仍走 4 家。
限流的店这一轮先记下，下一轮排到前面补，不在这一轮里干等。
"""
from __future__ import annotations

import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

_SHOP_WORKERS = 4
_QPS = 6
_QPS_GAP = 1 / _QPS
HOME_WORKERS = 12
_HOME_QPS_GAP = 1 / HOME_WORKERS
_SHOP: contextvars.ContextVar[int] = contextvars.ContextVar("pace_shop", default=0)
_LANE: contextvars.ContextVar[str] = contextvars.ContextVar("pace_lane", default="")
_LOCKS_GUARD = threading.Lock()
_SHOP_LOCKS: dict[int, threading.RLock] = {}
_ANON = threading.RLock()
_SLOTS = threading.Semaphore(_SHOP_WORKERS)
_HOME_SLOTS = threading.Semaphore(HOME_WORKERS)
_RATE = threading.Lock()
_HOME_RATE = threading.Lock()
_NEXT = 0.0
_HOME_NEXT = 0.0
_T = TypeVar("_T")


def too_often(message: str) -> bool:
    return "频繁" in (message or "")


def _blocked(row: dict) -> bool:
    text = str(row.get("sync_error") or "")
    return too_often(text) or "暂时连不上" in text


def _resume_page(row: dict) -> int:
    try:
        return int(row.get("resume_page") or 0)
    except (TypeError, ValueError):
        return 0


def _partial(rows: list[dict]) -> bool:
    return any(_resume_page(row) > 1 for row in rows)


def _shop_lock(shop_id: int) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _SHOP_LOCKS.get(shop_id)
        if lock is None:
            lock = threading.RLock()
            _SHOP_LOCKS[shop_id] = lock
        return lock


def _wait_turn(lock: threading.Lock, gap: float, clock: str) -> None:
    global _NEXT, _HOME_NEXT
    with lock:
        now = time.monotonic()
        if clock == "home":
            slot = _HOME_NEXT if _HOME_NEXT > now else now
            _HOME_NEXT = slot + gap
        else:
            slot = _NEXT if _NEXT > now else now
            _NEXT = slot + gap
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def call_paced(action: Callable[[], _T]) -> _T:
    """店内串行。默认店间最多 4 路、每秒 6 次。店铺首页这一路是 12 家。"""
    shop_id = int(_SHOP.get() or 0)
    lock = _shop_lock(shop_id) if shop_id else _ANON
    home = _LANE.get() == "home"
    slots = _HOME_SLOTS if home else _SLOTS
    with lock:
        slots.acquire()
        try:
            if home:
                _wait_turn(_HOME_RATE, _HOME_QPS_GAP, "home")
            else:
                _wait_turn(_RATE, _QPS_GAP, "")
            return action()
        finally:
            slots.release()


def take_turns(shop_ids: list[int], pull: Callable[[int], _T], workers: int | None = None, lane: str = "") -> None:
    """每家店只占一个线程，店内自己排队。默认同时 4 家，店铺首页传 12。"""
    if not shop_ids:
        return
    limit = workers if workers and workers > 0 else _SHOP_WORKERS
    pool_size = min(limit, len(shop_ids))

    def run(shop_id: int) -> None:
        token = _SHOP.set(int(shop_id))
        lane_token = _LANE.set(lane)
        try:
            pull(int(shop_id))
        finally:
            _SHOP.reset(token)
            _LANE.reset(lane_token)

    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = [pool.submit(run, shop_id) for shop_id in shop_ids]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                errors.append(exc)
    if errors:
        raise errors[0]


def prefer_limited(shop_ids: list[int], limited_ids: set[int]) -> list[int]:
    """上一轮被限流的店排在前面，趁队列还空的时候先补。"""
    return sorted(shop_ids, key=lambda shop_id: (0 if shop_id in limited_ids else 1, shop_id))


def limited_shop_ids(items: list) -> set[int]:
    found: set[int] = set()
    for row in items:
        if not isinstance(row, dict) or not _blocked(row):
            continue
        try:
            pk = int(row.get("store_pk") or 0)
        except (TypeError, ValueError):
            continue
        if pk:
            found.add(pk)
    return found


def merge_kept(old_items: list, new_items: list) -> list[dict]:
    """新结果只有「请求过于频繁」时，保留这家店上一次已经拉到的行。

    这一轮只拉到半截、上一轮已经完整的，也保留上一轮，避免半截结果把完整结果盖掉。
    """

    def pk_of(row: dict) -> int:
        try:
            return int(row.get("store_pk") or 0)
        except (TypeError, ValueError):
            return 0

    def grouped(items: list) -> dict[int, list[dict]]:
        buckets: dict[int, list[dict]] = {}
        for row in items:
            if not isinstance(row, dict):
                continue
            pk = pk_of(row)
            if pk:
                buckets.setdefault(pk, []).append(row)
        return buckets

    def complete(rows: list[dict]) -> bool:
        return bool(rows) and not _partial(rows) and any(not row.get("sync_error") for row in rows)

    old_by = grouped(old_items)
    new_by = grouped(new_items)
    order: list[int] = []
    seen: set[int] = set()
    for row in list(old_items) + list(new_items):
        if not isinstance(row, dict):
            continue
        pk = pk_of(row)
        if pk and pk not in seen:
            seen.add(pk)
            order.append(pk)
    merged: list[dict] = []
    for pk in order:
        fresh = new_by.get(pk) or []
        previous = old_by.get(pk) or []
        fresh_limited = bool(fresh) and all(_blocked(row) for row in fresh)
        previous_ready = any(not row.get("sync_error") for row in previous)
        if fresh_limited and previous_ready:
            merged.extend(previous)
        elif _partial(fresh) and complete(previous):
            merged.extend(previous)
        elif fresh:
            merged.extend(fresh)
        else:
            merged.extend(previous)
    return merged
