from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.services.auth import ensure_admin
from app.services.dy_proxy import browser_origin, build_proxy_response
from app.services.schedule_log import write_fail, write_start
from app.services.shop_service import (
    ensure_owner_account_column,
    ensure_shop_cookie_column,
    refresh_saved_accounts,
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
    """启动 20 秒后拉一次账户中心，之后按 DATA_REFRESH_HOURS 重复。金额写入 Redis。"""
    hours = _hours(get_settings().data_refresh_hours, 1)
    logger.info("账户中心定时更新已启动，20秒后第一次，之后每 %s 小时", hours)
    write_start(f"账户中心，20秒后第一次，之后每 {hours} 小时")
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(refresh_saved_accounts)
        except Exception:
            logger.exception("账户中心更新失败")
            write_fail("账户中心")
        hours = _hours(get_settings().data_refresh_hours, 1)
        _note_next("账户中心", hours)
        await asyncio.sleep(hours * 3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    try:
        Base.metadata.create_all(bind=engine)
        ensure_shop_cookie_column()
        ensure_owner_account_column()
        db = SessionLocal()
        try:
            ensure_admin(db)
        finally:
            db.close()
    except Exception:
        logger.exception("数据库还没连上，店铺列表暂时不可用")
    renew_task = asyncio.create_task(_renew_loop())
    refresh_task = asyncio.create_task(_refresh_loop())
    logger.info("%s 已启动，环境：%s", settings.app_name, settings.app_env)
    try:
        yield
    finally:
        renew_task.cancel()
        refresh_task.cancel()
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

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "dashboard.html")

    @app.get("/dyp/login", include_in_schema=False)
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
        """抖店页面认自己的路径。主站页面直接挂在本站同名路径上。"""
        return await proxy("fxg.jinritemai.com", full_path, request)

    return app


app = create_app()
