"""按 dy_product_day 里已有的全部日期，重算每个商品的退款率。不访问抖音。同一商品再跑会更新。

用法：
    D:\\fastapi-project\\douying-erp\\.venv\\Scripts\\python.exe D:\\fastapi-project\\douying-erp\\scripts\\sync_product_refund_rates.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
print("脚本已启动，正在加载", flush=True)

from app.db.session import engine  # noqa: E402
from app.services.product_refund_rate import rebuild_refund_rates  # noqa: E402

engine.echo = False


if __name__ == "__main__":
    print(rebuild_refund_rates(), flush=True)
