"""Convert source documents (DOCX/PDF) into markdown.

DOCX conversion walks paragraphs and tables in document order, mapping
``Heading N`` / ``Title`` styles to ATX heading levels and emitting a
``<!-- table:tbl_NNN -->`` placeholder for every top-level table (the
numbering matches ``extract.extract_tables_from_docx``, which iterates
``document.tables`` in the same document order).

PDF conversion uses PyMuPDF to pull page text spans, promotes larger-font
lines to headings using a simple font-size heuristic, and dumps embedded
images alongside the text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table as DocxTable

HEADING_STYLE_RE = re.compile(r"^Heading\s*(\d+)$", re.IGNORECASE)

_BLIP_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"


def _heading_level_from_style(style_name: str | None) -> int | None:
    if not style_name:
        return None
    name = style_name.strip()
    if name.lower() == "title":
        return 1
    m = HEADING_STYLE_RE.match(name)
    if m:
        return max(1, min(int(m.group(1)), 6))
    return None


def _paragraph_image_links(paragraph: Any, document: Any, image_dir: Path, counter: list[int]) -> list[str]:
    links: list[str] = []
    for blip in paragraph._element.findall(f".//{_BLIP_TAG}"):
        rid = blip.get(qn("r:embed"))
        if not rid:
            continue
        try:
            part = document.part.related_parts[rid]
        except KeyError:
            continue
        counter[0] += 1
        ext = Path(getattr(part, "partname", "")).suffix or ".png"
        name = f"img_{counter[0]:03d}{ext}"
        (image_dir / name).write_bytes(part.blob)
        links.append(f"![]({name})")
    return links


def convert_docx_to_markdown(path: str | Path, image_dir: str | Path) -> str:
    """Convert a ``.docx`` file to markdown text.

    Heading styles become ATX headings, images are saved under ``image_dir``
    with a markdown link inserted at their original position, and each
    top-level table is replaced by a ``<!-- table:tbl_NNN -->`` placeholder.
    """
    path = Path(path)
    image_dir = Path(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    document = Document(str(path))
    image_counter = [0]
    table_counter = 0
    lines: list[str] = []

    for item in document.iter_inner_content():
        if isinstance(item, DocxTable):
            table_counter += 1
            lines.append(f"<!-- table:tbl_{table_counter:03d} -->")
            lines.append("")
            continue

        paragraph = item
        text = paragraph.text.strip()
        level = _heading_level_from_style(paragraph.style.name if paragraph.style else None)
        image_links = _paragraph_image_links(paragraph, document, image_dir, image_counter)

        if text:
            lines.append(f"{'#' * level} {text}" if level else text)
        for link in image_links:
            lines.append(link)
        if text or image_links:
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _pdf_body_font_size(size_counts: dict[float, int]) -> float:
    if not size_counts:
        return 0.0
    return max(size_counts.items(), key=lambda kv: kv[1])[0]


def _pdf_heading_level(size: float, body_size: float, heading_sizes: list[float]) -> int | None:
    if body_size <= 0 or size <= body_size * 1.05:
        return None
    # Larger distinct sizes get smaller (more important) heading levels.
    for level, candidate in enumerate(heading_sizes, start=1):
        if abs(candidate - size) < 0.5:
            return min(level, 6)
    return 6


def convert_pdf_to_markdown(path: str | Path, image_dir: str | Path) -> str:
    """Convert a ``.pdf`` file to markdown text using PyMuPDF.

    Lines with a font size noticeably larger than the document's most common
    (body) font size are promoted to ATX headings, ranked by distinct font
    size (largest first). Embedded images are dumped under ``image_dir`` and
    referenced with a markdown link.
    """
    import fitz  # PyMuPDF

    path = Path(path)
    image_dir = Path(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(path))
    try:
        size_counts: dict[float, int] = {}
        pages: list[list[tuple[float, str]]] = []

        for page in doc:
            page_lines: list[tuple[float, str]] = []
            data = page.get_text("dict")
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(s.get("text", "") for s in spans).strip()
                    if not text:
                        continue
                    size = round(max((s.get("size", 0.0) for s in spans), default=0.0), 1)
                    page_lines.append((size, text))
                    size_counts[size] = size_counts.get(size, 0) + 1
            pages.append(page_lines)

        body_size = _pdf_body_font_size(size_counts)
        heading_sizes = sorted(
            {s for s in size_counts if s > body_size * 1.05},
            reverse=True,
        )

        image_counter = [0]
        lines: list[str] = []
        for page_index, page_lines in enumerate(pages):
            for size, text in page_lines:
                level = _pdf_heading_level(size, body_size, heading_sizes)
                lines.append(f"{'#' * level} {text}" if level else text)

            page = doc[page_index]
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    extracted = doc.extract_image(xref)
                except Exception:
                    continue
                image_counter[0] += 1
                ext = extracted.get("ext", "png")
                name = f"img_{image_counter[0]:03d}.{ext}"
                (image_dir / name).write_bytes(extracted["image"])
                lines.append(f"![]({name})")

            lines.append("")

        return "\n".join(lines).strip() + "\n"
    finally:
        doc.close()
