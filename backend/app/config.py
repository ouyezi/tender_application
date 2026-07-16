from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT
UPLOAD_DIR = DATA_DIR / "uploads"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "tender_diagnosis.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
# 招标/标书常见 1–2GB；默认单文件上限 2GB
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MOCK_ITEM_DELAY_SECONDS = 0.8
