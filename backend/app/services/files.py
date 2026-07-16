from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR

_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB，大文件上传更顺


def _max_upload_label() -> str:
    gib = MAX_UPLOAD_BYTES / (1024 ** 3)
    if gib >= 1 and abs(gib - round(gib)) < 1e-9:
        return f"{int(round(gib))}GB"
    mib = MAX_UPLOAD_BYTES / (1024 ** 2)
    return f"{int(round(mib))}MB"


def validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"only {', '.join(sorted(ALLOWED_EXTENSIONS))} allowed")
    return ext


async def save_upload(file: UploadFile, task_id: str, kind: str) -> tuple[str, str]:
    if not file.filename:
        raise HTTPException(400, f"{kind} file required")
    ext = validate_extension(file.filename)
    dest_dir = UPLOAD_DIR / task_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{kind}{ext}"
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, f"file too large (max {_max_upload_label()})")
            out.write(chunk)
    return file.filename, str(dest)
