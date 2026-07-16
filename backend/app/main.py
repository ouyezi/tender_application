from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.configs import router as configs_router
from app.api.tasks import router as tasks_router
from app.config import REPORT_DIR, UPLOAD_DIR
from app.db import init_db, recover_interrupted_tasks
from app.seed import seed_configs_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()
    await seed_configs_if_empty()
    await recover_interrupted_tasks()
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


@app.get("/api/health")
async def health():
    return {"ok": True}
