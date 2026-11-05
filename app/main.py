from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.schemas.common import ApiResponse

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logger.info("%s 已启动，环境：%s", settings.app_name, settings.app_env)
    yield
    logger.info("%s 已关闭", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

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

    @app.get("/", response_model=ApiResponse[dict[str, str]], include_in_schema=False)
    async def root() -> ApiResponse[dict[str, str]]:
        return ApiResponse(
            data={
                "name": settings.app_name,
                "docs": "/docs",
                "health": f"{settings.api_prefix}/health",
            }
        )

    return app


app = create_app()
