from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.parse import ocr as ocr_mod


def test_page_needs_ocr_when_sparse():
    assert ocr_mod.page_needs_ocr(" ", 50) is True
    assert ocr_mod.page_needs_ocr("", 10) is True
    assert ocr_mod.page_needs_ocr("x" * 49, 50) is True
    assert ocr_mod.page_needs_ocr("x" * 50, 50) is False


def test_ocr_page_used_when_native_text_sparse(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_mod, "ocr_image", lambda path: "扫描件授权证书正文")

    png_path = tmp_path / "p.png"
    Image.new("RGB", (8, 8), color="white").save(png_path)

    text = ocr_mod.maybe_ocr_page_text(native_text=" ", page_image_path=png_path)
    assert "授权证书" in text
    assert "<!-- source:ocr -->" in text


def test_maybe_ocr_page_text_keeps_native_when_sufficient(tmp_path):
    png_path = tmp_path / "p.png"
    Image.new("RGB", (8, 8), color="white").save(png_path)

    native = "本页已有足够长的原生文本内容，不需要 OCR 补充识别。"
    text = ocr_mod.maybe_ocr_page_text(native_text=native, page_image_path=png_path)
    assert text == native
    assert "<!-- source:ocr -->" not in text


def test_convert_pdf_uses_ocr_for_sparse_page(monkeypatch, tmp_path):
    import fitz

    from app.services.parse.convert import convert_pdf_to_markdown

    pdf_path = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), " ")
    doc.save(str(pdf_path))
    doc.close()

    monkeypatch.setattr(
        "app.services.parse.ocr.maybe_ocr_page_text",
        lambda native_text, page_image_path, **kwargs: "扫描件授权证书正文\n\n<!-- source:ocr -->",
    )

    image_dir = tmp_path / "images"
    markdown = convert_pdf_to_markdown(pdf_path, image_dir)
    assert "授权证书" in markdown
    assert "<!-- source:ocr -->" in markdown
