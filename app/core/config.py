from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量和 .env 读取应用配置。"""

    app_name: str = "Douying ERP"
    app_env: str = "dev"
    debug: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: str = "*"
    database_url: str = "mysql+pymysql://root@127.0.0.1:3306/douying_erp?charset=utf8mb4"
    redis_url: str = "redis://127.0.0.1:6379/1"
    fetch_workers: int = 3
    cookie_renew_hours: int = 2
    data_refresh_hours: int = 1
    secret_key: str = "change-me"
    token_expire_minutes: int = 720
    init_admin_username: str = "admin"
    init_admin_password: str = "admin123"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
