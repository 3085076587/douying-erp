from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.auth import ensure_admin
from app.services.dy_proxy import browser_origin, build_proxy_response
from app.services.schedule_log import write_fail, write_start, write_success
from app.services import violation_service
from app.services.center_service import (
    center_cache_fresh,
    center_schedule,
    load_center_cache,
    overview_cache_fresh,
    run_center_cycle,
    run_overview_cycle,
    touch_daily,
)
from app.services.home_service import cache_fresh as home_cache_fresh
from app.services.home_service import next_home_wait, refresh_home
from app.services import invoice_service
from app.services.account_service import live_cache_fresh, refresh_saved_accounts
from app.services.store_service import (
    ensure_credential_at_column,
    ensure_owner_account_column,
    ensure_shop_cookie_column,
    renew_cookies,
)
import app.models  # noqa: F401

logger = logging.getLogger("uvicorn.error")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _hours(value: int | None, default: int) -> int:
    return max(1, int(value or default))


def _note_next(label: str, hours: int) -> None:
    nxt = datetime.now() + timedelta(hours=hours)
    text = f"{label}下次更新 {nxt.strftime('%Y-%m-%d %H:%M')}"
    logger.info(text)
    write_start(text)


async def _renew_loop() -> None:
    """启动 15 秒后给每家已有凭证的店续一次登录态，之后按 COOKIE_RENEW_HOURS 重复。"""
    hours = _hours(get_settings().cookie_renew_hours, 2)
    logger.info("登录凭证维护已启动，15秒后第一次，之后每 %s 小时", hours)
    write_start(f"登录凭证维护，15秒后第一次，之后每 {hours} 小时")
    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(renew_cookies)
        except Exception:
            logger.exception("登录凭证维护失败")
            write_fail("登录凭证维护")
        hours = _hours(get_settings().cookie_renew_hours, 2)
        _note_next("登录凭证维护", hours)
        await asyncio.sleep(hours * 3600)


async def _refresh_loop() -> None:
    """启动后拉一次账户中心，之后按 DATA_REFRESH_HOURS 重复。10 分钟内 Redis 已有金额则跳过这次预热。"""
    hours = _hours(get_settings().data_refresh_hours, 1)
    logger.info("账户中心定时更新已启动，20秒后第一次，之后每 %s 小时", hours)
    write_start(f"账户中心，20秒后第一次，之后每 {hours} 小时")
    if live_cache_fresh():
        write_success("账户中心 10分钟内已有数据，启动预热跳过")
        logger.info("账户中心 10分钟内已有数据，启动预热跳过")
    else:
        await asyncio.sleep(20)
        try:
            await asyncio.to_thread(refresh_saved_accounts)
        except Exception:
            logger.exception("账户中心更新失败")
            write_fail("账户中心")
    while True:
        try:
            hours = _hours(get_settings().data_refresh_hours, 1)
            _note_next("账户中心", hours)
            await asyncio.sleep(hours * 3600)
            await asyncio.to_thread(refresh_saved_accounts)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("账户中心更新失败")
            write_fail("账户中心")
            await asyncio.sleep(60)


async def _violation_once(minutes: int) -> None:
    write_start("违规与预警")
    await asyncio.to_thread(violation_service.run_refresh_sync, "", "", "schedule")
    state = violation_service.progress()
    message = str(state.get("message") or "完成")
    failed = int(state.get("failed") or 0)
    if failed:
        write_fail("违规与预警", message)
        logger.warning("违规与预警定时更新有失败：%s", message)
    else:
        write_success(f"违规与预警 {message}")
    logger.info("违规与预警下次更新 %s", (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M"))


async def _violation_loop() -> None:
    """违规与预警定时更新：启动后先预热一次，之后每 VIOLATION_REFRESH_MINUTES 分钟再拉。

    - 启动预热：Redis 里这份数据还在 10 分钟内就跳过，否则约 20 秒后开始拉；
    - 预热完成后仍按上次更新时间推算下一轮，间隔不变；
    - 每次开始/成功/失败都写 logs/schedule.log，更新完成时间写 logs/violation_scheduler_state.json；
    - 页面上的「数据10分钟更新一次，上一次更新 xxx，下次更新 xxx」就读这个状态文件。
    """
    minutes = violation_service.schedule_minutes()
    logger.info("违规与预警定时更新已启动，启动后预热一次，之后每 %s 分钟一次", minutes)
    write_start(f"违规与预警，启动后预热，之后每 {minutes} 分钟一次")
    first = True
    while True:
        try:
            if first:
                first = False
                if violation_service.cache_fresh():
                    write_success("违规与预警 10分钟内已有数据，启动预热跳过")
                    logger.info("违规与预警 10分钟内已有数据，启动预热跳过")
                else:
                    violation_service.begin_warmup()
                    await asyncio.sleep(20)
                    await _violation_once(minutes)
                    continue
            wait_seconds = 20.0
            last = str(violation_service.read_state().get("last_update") or "")
            if last:
                try:
                    last_at = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    wait_seconds = max(5.0, (last_at + timedelta(minutes=minutes) - datetime.now()).total_seconds())
                except ValueError:
                    wait_seconds = 20.0
            await asyncio.sleep(wait_seconds)
            await _violation_once(minutes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("违规与预警定时更新失败")
            write_fail("违规与预警")
            await asyncio.sleep(60)


async def _center_loop() -> None:
    """店铺明细、商品明细：启动后先预热，之后每 10 分钟写入 Redis。10 分钟内已有数据则跳过这次预热。"""
    logger.info("店铺明细、商品明细已启动，20秒后预热，之后每 10 分钟写入 Redis")
    write_start("店铺明细、商品明细，20秒后预热，之后每 10 分钟")
    if center_cache_fresh():
        write_success("店铺明细、商品明细 10分钟内已有数据，启动预热跳过")
        logger.info("店铺明细、商品明细 10分钟内已有数据，启动预热跳过")
    else:
        await asyncio.sleep(20)
        try:
            write_start("店铺明细、商品明细")
            await asyncio.to_thread(run_center_cycle)
            write_success("店铺明细、商品明细已写入 Redis")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("店铺明细、商品明细更新失败")
            write_fail("店铺明细、商品明细")
    while True:
        try:
            state = center_schedule()
            logger.info("店铺明细、商品明细下次更新 %s", state.get("next_update") or "")
            wait_seconds = 600.0
            nxt = str(state.get("next_update") or "")
            if nxt:
                try:
                    nxt_at = datetime.strptime(nxt, "%Y-%m-%d %H:%M")
                    wait_seconds = max(5.0, (nxt_at - datetime.now()).total_seconds())
                except ValueError:
                    wait_seconds = 600.0
            await asyncio.sleep(wait_seconds)
            write_start("店铺明细、商品明细")
            await asyncio.to_thread(run_center_cycle)
            write_success("店铺明细、商品明细已写入 Redis")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("店铺明细、商品明细更新失败")
            write_fail("店铺明细、商品明细")
            await asyncio.sleep(60)


def _run_daily(pull_home: bool = True, pull_overview: bool = True) -> str:
    failed = 0
    if pull_home:
        home = refresh_home()
        failed += int(home.get("failed") or 0)
    if pull_overview:
        touch_daily()
        overview = run_overview_cycle()
        failed += int(overview.get("failed") or 0)
    if not failed:
        return ""
    home_failed = int(home.get("failed") or 0) if pull_home else 0
    overview_failed = int(overview.get("failed") or 0) if pull_overview else 0
    return f"店铺首页 {home_failed} 家没拉到，数据总览 {overview_failed} 家次没拉到"


def _next_daily_text() -> str:
    nxt = datetime.now() + timedelta(seconds=next_home_wait())
    return nxt.strftime("%Y-%m-%d %H:%M")


async def _invoice_loop() -> None:
    """发票数量：启动后先预热，之后每 10 分钟写入 Redis。10 分钟内已有数据则跳过这次预热。"""
    logger.info("发票管理已启动，20秒后预热，之后每 10 分钟写入 Redis")
    write_start("发票管理，20秒后预热，之后每 10 分钟")
    if invoice_service.cache_fresh():
        write_success("发票管理 10分钟内已有数据，启动预热跳过")
        logger.info("发票管理 10分钟内已有数据，启动预热跳过")
    else:
        await asyncio.sleep(20)
        await _invoice_once()
    while True:
        try:
            logger.info("发票管理下次更新 %s", (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M"))
            await asyncio.sleep(600)
            await _invoice_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("发票管理更新失败")
            write_fail("发票管理")
            await asyncio.sleep(60)


async def _invoice_once() -> None:
    try:
        write_start("发票管理")
        result = await asyncio.to_thread(invoice_service.run_scheduled)
        message = str(result.get("message") or "完成")
        failed = int(result.get("failed") or 0)
        if result.get("skipped"):
            write_success(f"发票管理 {message}")
        elif failed:
            write_fail("发票管理", message)
        else:
            write_success(f"发票管理已写入 Redis，{message}")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("发票管理更新失败")
        write_fail("发票管理")


async def _home_loop() -> None:
    """数据总览和店铺首页：启动后先预热，之后每天 0 点和 12 点写入 Redis。10 分钟内已有数据则跳过这次预热。"""
    logger.info("数据总览、店铺首页已启动，25秒后预热，之后每天 0 点和 12 点写入 Redis")
    write_start("数据总览、店铺首页，25秒后预热，之后每天 0 点和 12 点")
    home_ok = home_cache_fresh()
    overview_ok = overview_cache_fresh()
    if home_ok and overview_ok:
        write_success("数据总览、店铺首页 10分钟内已有数据，启动预热跳过")
        logger.info("数据总览、店铺首页 10分钟内已有数据，启动预热跳过")
    else:
        await asyncio.sleep(25)
        try:
            write_start("数据总览、店铺首页")
            detail = await asyncio.to_thread(_run_daily, not home_ok, not overview_ok)
            if detail:
                write_fail("数据总览、店铺首页", detail)
            else:
                write_success("数据总览、店铺首页已写入 Redis")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("数据总览、店铺首页更新失败")
            write_fail("数据总览、店铺首页")
    while True:
        try:
            nxt = _next_daily_text()
            logger.info("数据总览、店铺首页下次更新 %s", nxt)
            await asyncio.sleep(next_home_wait())
            write_start("数据总览、店铺首页")
            detail = await asyncio.to_thread(_run_daily)
            if detail:
                write_fail("数据总览、店铺首页", detail)
            else:
                write_success("数据总览、店铺首页已写入 Redis")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("数据总览、店铺首页更新失败")
            write_fail("数据总览、店铺首页")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    try:
        Base.metadata.create_all(bind=engine)
        ensure_shop_cookie_column()
        ensure_owner_account_column()
        ensure_credential_at_column()
        db = SessionLocal()
        try:
            ensure_admin(db)
        finally:
            db.close()
    except Exception:
        logger.exception("数据库还没连上，店铺列表暂时不可用")
    try:
        load_center_cache()
    except Exception:
        logger.exception("店铺明细、商品明细缓存没读出来")
    renew_task = asyncio.create_task(_renew_loop())
    center_task = asyncio.create_task(_center_loop())
    refresh_task = asyncio.create_task(_refresh_loop())
    violation_task = asyncio.create_task(_violation_loop())
    home_task = asyncio.create_task(_home_loop())
    invoice_task = asyncio.create_task(_invoice_loop())
    logger.info("%s 已启动，环境：%s", settings.app_name, settings.app_env)
    try:
        yield
    finally:
        renew_task.cancel()
        center_task.cancel()
        refresh_task.cancel()
        violation_task.cancel()
        home_task.cancel()
        invoice_task.cancel()
        logger.info("%s 已关闭", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    origins = settings.cors_origin_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def log_api_call(request: Request, call_next):
        """接口访问日志，只记路径和状态码，不记响应内容（避免把登录令牌写进文件）。"""
        response = await call_next(request)
        if request.url.path.startswith(settings.api_prefix):
            try:
                log_dir = Path(__file__).resolve().parent.parent / "logs"
                log_dir.mkdir(exist_ok=True)
                with (log_dir / "api.log").open("a", encoding="utf-8") as fh:
                    fh.write(
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} {request.method} {request.url.path}"
                        f"?{request.url.query} -> {response.status_code}"
                        f" [{response.headers.get('content-type', '')}]\n"
                    )
            except Exception:
                pass
        return response

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "login.html")

    @app.api_route("/dashboard", methods=["GET", "HEAD"], include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "dashboard.html")

    @app.api_route("/dyp/login", methods=["GET", "HEAD"], include_in_schema=False)
    def embedded_login():
        return RedirectResponse("/login/common?channel=zhaoshang")

    async def proxy(host: str, path: str, request: Request) -> Response:
        origin = browser_origin(
            request.headers.get("host", ""),
            request.url.scheme,
            request.headers.get("referer", ""),
            request.headers.get("origin", ""),
        ) or f"{request.url.scheme}://127.0.0.1"
        try:
            status, payload, content_type, header_items = build_proxy_response(
                method=request.method,
                host=host,
                upstream_path=path,
                query=request.url.query,
                request_headers={key.lower(): value for key, value in request.headers.items()},
                body=await request.body(),
                origin=origin,
            )
        except PermissionError:
            return Response("这个地址不能转发", status_code=403)
        except RuntimeError as exc:
            return Response(str(exc), status_code=502)
        media = content_type.split(";", 1)[0].strip() or "application/octet-stream"
        response = Response(content=payload, status_code=status, media_type=media)
        for key, value in header_items:
            response.headers.append(key, value)
        return response

    @app.api_route("/dyp/{host}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def mounted_proxy_root(host: str, request: Request) -> Response:
        return await proxy(host, "", request)

    @app.api_route("/dyp/{host}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def mounted_proxy(host: str, path: str, request: Request) -> Response:
        return await proxy(host, path, request)

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def shop_page(full_path: str, request: Request) -> Response:
        """抖店页面认自己的路径。主站页面直接挂在本站同名路径上。

        以 api/ static/ 开头的是本站自己的地址，找不到就明确返回 404，
        不能转给抖店——否则前端拿到的是一段 HTML，报错会变得看不懂。
        """
        if full_path.startswith(("api/", "static/")):
            return JSONResponse({"detail": "接口不存在"}, status_code=404)
        return await proxy("fxg.jinritemai.com", full_path, request)

    return app


app = create_app()