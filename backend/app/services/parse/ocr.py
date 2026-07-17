"""OCR helpers for PDF pages with little or no native text."""

from __future__ import annotations

from pathlib import Path

DEFAULT_MIN_OCR_CHARS = 50


def page_needs_ocr(native_text: str, min_chars: int) -> bool:
    return len((native_text or "").strip()) < min_chars


def ocr_image(path: Path) -> str:
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(path), lang="chi_sim+eng")


def maybe_ocr_page_text(
    native_text: str,
    page_image_path: Path,
    *,
    min_chars: int = DEFAULT_MIN_OCR_CHARS,
    warnings: list[str] | None = None,
) -> str:
    """Return native text, optionally augmented with OCR when the page is sparse."""
    text = (native_text or "").strip()
    if not page_needs_ocr(native_text, min_chars):
        return native_text or ""
    if not page_image_path.is_file():
        return native_text or ""
    try:
        ocr_text = ocr_image(page_image_path).strip()
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"ocr_failed:{page_image_path.name}:{exc}")
        return native_text or ""
    if not ocr_text:
        return native_text or ""

    parts: list[str] = []
    if text:
        parts.append(text)
    parts.append(ocr_text)
    parts.append("<!-- source:ocr -->")
    return "\n\n".join(parts)
