"""把每家店 2026-10-01 到昨天的商品按自然日写入 dy_product_day。同一天再跑会覆盖。

用法：
    D:\\fastapi-project\\douying-erp\\.venv\\Scripts\\python.exe D:\\fastapi-project\\douying-erp\\scripts\\sync_product_days.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
print("脚本已启动，正在加载", flush=True)

from app.db.session import engine  # noqa: E402
from app.services.product_sync import backfill_since_october  # noqa: E402

engine.echo = False


if __name__ == "__main__":
    backfill_since_october()
