# Douying ERP

基于 FastAPI 的后端骨架。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

## 目录

```
app/
  main.py                 应用入口
  core/config.py          配置
  core/exceptions.py      统一异常
  schemas/common.py       统一响应
  api/v1/router.py        v1 路由汇总
  api/v1/endpoints/       具体接口
```

新接口写在 `app/api/v1/endpoints/`，再在 `app/api/v1/router.py` 里挂上。
