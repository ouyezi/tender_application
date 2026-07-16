from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR


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
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "file too large (max 50MB)")
            out.write(chunk)
    return file.filename, str(dest)
