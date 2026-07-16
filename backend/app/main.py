from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.configs import router as configs_router
from app.api.tasks import router as tasks_router
from app.config import REPORT_DIR, UPLOAD_DIR
from app.db import init_db, recover_interrupted_parse_jobs, recover_interrupted_tasks
from app.seed import seed_configs_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()
    await seed_configs_if_empty()
    await recover_interrupted_tasks()
    await recover_interrupted_parse_jobs()
    from app.services import parse_scheduler

    await parse_scheduler.kick()
    yield


app = FastAPI(title="Tender Diagnosis Demo", lifespan=lifespan)
app.include_router(configs_router)
app.include_router(tasks_router)
# Demo 无鉴权：允许局域网其他机器通过本机 IP 访问前端时的跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """避免访问 API 根路径时只看到裸 404；引导到前端。"""
    return {
        "service": "tender-diagnosis-api",
        "message": "这是后端 API。请打开前端页面 http://<主机IP>:5555/ ，不要只访问 :8888/",
        "health": "/api/health",
        "docs": "/docs",
    }


@app.get("/api/health")
async def health():
    return {"ok": True}
