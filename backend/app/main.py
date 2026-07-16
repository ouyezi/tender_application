from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import REPORT_DIR, UPLOAD_DIR

app = FastAPI(title="Tender Diagnosis Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
async def health():
    return {"ok": True}
