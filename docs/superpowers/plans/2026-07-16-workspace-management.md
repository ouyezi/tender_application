# 工作区管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以诊断任务 ID 为 Artifact，提供工作区文件管理、异步文档解析（markdown/图片/表格/文档树/分块）及章节树浏览页；本期不做检索。

**Architecture:** 新增 `WorkspaceFile` / `ParseJob` 与独立 Parse scheduler；磁盘按 `uploads/{task_id}/{document|markdown|image|table|json|report|other}/` + `index.md` 组织；管线 `convert → extract → build_tree → chunk → write_index`；前端 `/workspaces` 列表与详情（文件列表 + 左树右文）。

**Tech Stack:** FastAPI + SQLAlchemy + asyncio；`python-docx`（已有）+ `pymupdf`（PDF）；React + Vite；复用 `MarkdownPreview`。

**Spec:** `docs/superpowers/specs/2026-07-16-workspace-management-design.md`

---

## File Structure

```text
backend/app/
  config.py                         # + 解析相关常量（可选）
  models.py                         # + WorkspaceFile, ParseJob; DiagnosisTask tender/bid_file_id
  schemas.py                        # + workspace schemas
  db.py                             # + recover_interrupted_parse_jobs
  main.py                           # + workspaces router; recover parse jobs on startup
  api/
    workspaces.py                   # NEW workspace REST
    tasks.py                        # create_task 挂钩 register + enqueue parse
  services/
    files.py                        # 保留；workspace 导入另走 artifact
    artifact.py                     # NEW 目录布局、迁入 document、index.md
    workspace.py                    # NEW 注册文件、入队、读树/切片
    parse_scheduler.py              # NEW 异步解析调度
    parse/
      __init__.py
      pipeline.py                   # NEW 编排各阶段
      convert.py                    # NEW docx/pdf → markdown (+ 抽图入口)
      extract.py                    # NEW 图片路径规范化 + 表格 HTML/CSV
      tree.py                       # NEW TOC/标题/序号 → 文档树
      chunk.py                      # NEW 分块
      index_md.py                   # NEW 写 index.md / meta.json

backend/tests/
  fixtures/
    sample_with_toc.md              # NEW 含目录页的 markdown 夹具
    sample_merged_table.docx        # NEW 或用代码生成
  test_artifact.py                  # NEW
  test_workspace_register.py        # NEW
  test_parse_tree.py                # NEW
  test_parse_extract_table.py       # NEW
  test_parse_pipeline.py            # NEW
  test_workspaces_api.py            # NEW
  conftest.py                       # + monkeypatch UPLOAD_DIR for workspace; reset parse_scheduler

frontend/src/
  api.js                            # + workspace API helpers
  App.jsx                           # + /workspaces routes
  pages/
    WorkspaceListPage.jsx           # NEW
    WorkspaceDetailPage.jsx         # NEW
  components/
    DocumentTree.jsx                # NEW
    ImportFileModal.jsx             # NEW
  pages/TaskDetailPage.jsx          # + 打开工作区链接
  pages/TaskListPage.jsx            # + 导航「工作区」
  App.css                           # + workspace 布局样式

backend/requirements.txt            # + pymupdf
```

---

### Task 1: Models — WorkspaceFile / ParseJob / task file ids

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_db.py`（若有列断言则扩展；否则本任务以 create_all 冒烟为准）

- [ ] **Step 1: Extend `DiagnosisTask`**

In `backend/app/models.py`, after `bid_path` add:

```python
    tender_file_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    bid_file_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
```

- [ ] **Step 2: Add `WorkspaceFile` and `ParseJob`**

Append to `backend/app/models.py`:

```python
class WorkspaceFile(Base):
    __tablename__ = "workspace_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("diagnosis_tasks.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="document")  # document | other
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending | running | succeeded | failed | partial | skipped
    parse_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tree_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    md_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    chunks_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ParseJob(Base):
    __tablename__ = "parse_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspace_files.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # queued | running | succeeded | failed
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="convert")
    # convert | extract | build_tree | chunk | write_index
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: Add workspace schemas**

Append to `backend/app/schemas.py`:

```python
class WorkspaceFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    label: str
    original_filename: str
    kind: str
    ext: str
    parse_status: str
    parse_error: Optional[str]
    created_at: datetime
    updated_at: datetime


class WorkspaceListItem(BaseModel):
    task_id: str
    tender_filename: str
    bid_filename: str
    file_count: int
    parse_succeeded: int
    parse_running: int
    parse_failed: int
    created_at: datetime


class WorkspaceDetailOut(BaseModel):
    task_id: str
    tender_filename: str
    bid_filename: str
    files: List[WorkspaceFileOut]


class TreeNodeOut(BaseModel):
    id: str
    title: str
    level: int
    numbering: str = ""
    parent_id: Optional[str] = None
    start_offset: int
    end_offset: int
    self_start: int
    subtree_end: int
    source: str = "heading"
    children: List["TreeNodeOut"] = Field(default_factory=list)


TreeNodeOut.model_rebuild()


class ContentOut(BaseModel):
    node_id: str
    title: str
    markdown: str
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py backend/app/schemas.py
git commit -m "feat: add WorkspaceFile and ParseJob models"
```

---

### Task 2: Artifact layout helpers

**Files:**
- Create: `backend/app/services/artifact.py`
- Create: `backend/tests/test_artifact.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_artifact.py`:

```python
from pathlib import Path

from app.services import artifact


def test_ensure_artifact_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    root = artifact.ensure_artifact_dirs("T-TEST-001")
    for name in ("document", "markdown", "image", "table", "json", "report", "other"):
        assert (root / name).is_dir()
    assert root == tmp_path / "T-TEST-001"


def test_move_into_document(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    task_id = "T-TEST-002"
    root = artifact.ensure_artifact_dirs(task_id)
    src = root / "tender.docx"
    src.write_bytes(b"fake")
    dest = artifact.move_into_document(task_id, src, file_id="fid01", original_name="招标.docx")
    assert dest.is_file()
    assert dest.parent == root / "document"
    assert "fid01" in dest.name
    assert not src.exists()


def test_write_index_md(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path)
    task_id = "T-TEST-003"
    artifact.ensure_artifact_dirs(task_id)
    artifact.write_index_md(
        task_id,
        [
            {
                "file_id": "abc",
                "label": "招标文件",
                "original_filename": "a.docx",
                "kind": "document",
                "parse_status": "pending",
                "md_path": "",
                "tree_path": "",
                "warnings": "",
            }
        ],
    )
    text = (tmp_path / task_id / "index.md").read_text(encoding="utf-8")
    assert "abc" in text
    assert "招标文件" in text
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_artifact.py -v`  
Expected: FAIL（`artifact` 模块不存在）

- [ ] **Step 3: Implement `artifact.py`**

Create `backend/app/services/artifact.py`:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from app.config import UPLOAD_DIR

ARTIFACT_SUBDIRS = ("document", "markdown", "image", "table", "json", "report", "other")


def artifact_root(task_id: str) -> Path:
    return UPLOAD_DIR / task_id


def ensure_artifact_dirs(task_id: str) -> Path:
    root = artifact_root(task_id)
    root.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _safe_name(name: str) -> str:
    base = Path(name).name
    return re.sub(r"[^\w.\u4e00-\u9fff\-]+", "_", base)[:180] or "file"


def move_into_document(task_id: str, src: Path, *, file_id: str, original_name: str) -> Path:
    root = ensure_artifact_dirs(task_id)
    ext = Path(original_name).suffix.lower() or src.suffix.lower()
    dest = root / "document" / f"{file_id}_{_safe_name(original_name)}"
    if dest.suffix.lower() != ext and ext:
        dest = dest.with_suffix(ext)
    src = Path(src)
    if src.resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dest)
    return dest


def write_index_md(task_id: str, files: Iterable[dict[str, Any]]) -> Path:
    root = ensure_artifact_dirs(task_id)
    lines = [
        f"# Workspace Index — {task_id}",
        "",
        "| file_id | label | filename | kind | status | markdown | tree |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in files:
        lines.append(
            "| {file_id} | {label} | {original_filename} | {kind} | {parse_status} | {md_path} | {tree_path} |".format(
                file_id=f.get("file_id", ""),
                label=f.get("label", ""),
                original_filename=f.get("original_filename", ""),
                kind=f.get("kind", ""),
                parse_status=f.get("parse_status", ""),
                md_path=f.get("md_path") or "",
                tree_path=f.get("tree_path") or "",
            )
        )
        if f.get("warnings"):
            lines.append(f"")
            lines.append(f"- warnings ({f['file_id']}): {f['warnings']}")
    path = root / "index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_artifact.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/artifact.py backend/tests/test_artifact.py
git commit -m "feat: add artifact directory layout helpers"
```

---

### Task 3: Register workspace files + enqueue ParseJob

**Files:**
- Create: `backend/app/services/workspace.py`
- Create: `backend/tests/test_workspace_register.py`
- Modify: `backend/app/api/tasks.py`（create_task 调用注册；暂不跑真实解析，enqueue 可先写 DB + 空调度钩子）

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_workspace_register.py`:

```python
import pytest
from sqlalchemy import select

from app.models import DiagnosisTask, ParseJob, WorkspaceFile
from app.services import artifact, workspace


@pytest.mark.asyncio
async def test_register_task_documents(tmp_path, monkeypatch, client):
    # client fixture creates DB; use SessionLocal after create task files manually
    from app import db as database

    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(workspace, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(exist_ok=True)

    task_id = "T-REG-001"
    root = artifact.ensure_artifact_dirs(task_id)
    tender = root / "tender.docx"
    bid = root / "bid.docx"
    tender.write_bytes(b"t")
    bid.write_bytes(b"b")

    async with database.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="tender.docx",
                tender_path=str(tender),
                bid_filename="bid.docx",
                bid_path=str(bid),
                status="interpreting",
                config_snapshot="[]",
            )
        )
        await session.commit()

        tender_f, bid_f = await workspace.register_task_documents(
            session,
            task_id=task_id,
            tender_path=str(tender),
            tender_filename="tender.docx",
            bid_path=str(bid),
            bid_filename="bid.docx",
        )
        await session.commit()

        task = await session.get(DiagnosisTask, task_id)
        assert task.tender_file_id == tender_f.id
        assert task.bid_file_id == bid_f.id
        assert Path(task.tender_path).parent.name == "document"

        files = (await session.execute(select(WorkspaceFile).where(WorkspaceFile.task_id == task_id))).scalars().all()
        assert len(files) == 2
        jobs = (await session.execute(select(ParseJob).where(ParseJob.task_id == task_id))).scalars().all()
        assert len(jobs) == 2
        assert all(j.status == "queued" for j in jobs)
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_workspace_register.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `workspace.register_task_documents` and `enqueue_parse`**

Create `backend/app/services/workspace.py` with at least:

```python
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UPLOAD_DIR  # re-export for tests monkeypatch if needed
from app.models import DiagnosisTask, ParseJob, WorkspaceFile, utcnow
from app.services import artifact


def new_file_id() -> str:
    return uuid.uuid4().hex[:12]


async def enqueue_parse(session: AsyncSession, wf: WorkspaceFile, *, attempt: int = 1) -> ParseJob:
    job = ParseJob(
        file_id=wf.id,
        task_id=wf.task_id,
        status="queued",
        stage="convert",
        attempt=attempt,
    )
    wf.parse_status = "pending"
    wf.updated_at = utcnow()
    session.add(job)
    await session.flush()
    return job


async def register_task_documents(
    session: AsyncSession,
    *,
    task_id: str,
    tender_path: str,
    tender_filename: str,
    bid_path: str,
    bid_filename: str,
) -> tuple[WorkspaceFile, WorkspaceFile]:
    artifact.ensure_artifact_dirs(task_id)
    pairs = [
        ("招标文件", tender_path, tender_filename, "tender"),
        ("标书", bid_path, bid_filename, "bid"),
    ]
    created: list[WorkspaceFile] = []
    task = await session.get(DiagnosisTask, task_id)
    for label, path, filename, role in pairs:
        fid = new_file_id()
        dest = artifact.move_into_document(
            task_id, Path(path), file_id=fid, original_name=filename
        )
        wf = WorkspaceFile(
            id=fid,
            task_id=task_id,
            label=label,
            original_filename=filename,
            stored_path=str(dest),
            kind="document",
            ext=dest.suffix.lower(),
            parse_status="pending",
        )
        session.add(wf)
        await session.flush()
        await enqueue_parse(session, wf)
        created.append(wf)
        if task is not None:
            if role == "tender":
                task.tender_file_id = fid
                task.tender_path = str(dest)
            else:
                task.bid_file_id = fid
                task.bid_path = str(dest)
    await artifact_refresh_index(session, task_id)
    return created[0], created[1]


async def artifact_refresh_index(session: AsyncSession, task_id: str) -> None:
    from sqlalchemy import select

    rows = (
        await session.execute(select(WorkspaceFile).where(WorkspaceFile.task_id == task_id))
    ).scalars().all()
    artifact.write_index_md(
        task_id,
        [
            {
                "file_id": r.id,
                "label": r.label,
                "original_filename": r.original_filename,
                "kind": r.kind,
                "parse_status": r.parse_status,
                "md_path": r.md_path or "",
                "tree_path": r.tree_path or "",
                "warnings": r.parse_error or "",
            }
            for r in rows
        ],
    )
```

- [ ] **Step 4: Hook `create_task`**

In `backend/app/api/tasks.py`, after `db.add(task)` / before or after commit — **after commit + refresh**, call:

```python
    await db.commit()
    await db.refresh(task)

    tender_f, bid_f = await workspace.register_task_documents(
        db,
        task_id=task_id,
        tender_path=tender_path,
        tender_filename=tender_filename,
        bid_path=bid_path,
        bid_filename=bid_filename,
    )
    await db.commit()
    await db.refresh(task)

    await scheduler.start_task(task_id)
    # Parse scheduler kick will be added in Task 5:
    # await parse_scheduler.kick()
```

Import `from app.services import workspace`.

- [ ] **Step 5: Run tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_workspace_register.py tests/test_tasks.py -v`  
Expected: PASS（若 `test_tasks` 断言路径含 `tender.docx` 文件名，改为断言路径在 `document/` 下或更新断言）

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/workspace.py backend/app/api/tasks.py backend/tests/test_workspace_register.py backend/tests/test_tasks.py
git commit -m "feat: register workspace files on task create"
```

---

### Task 4: Document tree builder (TOC-aware)

**Files:**
- Create: `backend/app/services/parse/__init__.py`
- Create: `backend/app/services/parse/tree.py`
- Create: `backend/tests/fixtures/sample_with_toc.md`
- Create: `backend/tests/test_parse_tree.py`

- [ ] **Step 1: Add fixture**

Create `backend/tests/fixtures/sample_with_toc.md`:

```markdown
# 目录

1. 第一章 总则 ............ 1
1.1 目的 ................ 1
2. 第二章 要求 ............ 3

# 1 第一章 总则

总则正文。

## 1.1 目的

目的正文。

# 2 第二章 要求

要求正文。
```

- [ ] **Step 2: Write failing tests**

```python
from pathlib import Path

from app.services.parse.tree import build_document_tree, flatten_nodes


FIXTURE = Path(__file__).parent / "fixtures" / "sample_with_toc.md"


def test_toc_region_not_in_section_nodes():
    md = FIXTURE.read_text(encoding="utf-8")
    tree = build_document_tree(md)
    nodes = flatten_nodes(tree)
    titles = [n["title"] for n in nodes]
    assert "目录" not in titles
    assert any("总则" in t for t in titles)
    assert any("目的" in t for t in titles)


def test_subtree_end_covers_children():
    md = FIXTURE.read_text(encoding="utf-8")
    tree = build_document_tree(md)
    nodes = {n["id"]: n for n in flatten_nodes(tree)}
    chapter1 = next(n for n in nodes.values() if "总则" in n["title"] and n["level"] <= 2)
    child = next(n for n in nodes.values() if n.get("parent_id") == chapter1["id"])
    assert chapter1["subtree_end"] >= child["end_offset"]
    assert chapter1["self_start"] <= chapter1["start_offset"]


def test_numbering_levels():
    md = FIXTURE.read_text(encoding="utf-8")
    nodes = flatten_nodes(build_document_tree(md))
    purpose = next(n for n in nodes if "目的" in n["title"])
    assert purpose["numbering"].startswith("1.1") or purpose["level"] >= 2
```

- [ ] **Step 3: Run to verify fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_parse_tree.py -v`  
Expected: FAIL

- [ ] **Step 4: Implement `tree.py`**

实现要点（写入 `backend/app/services/parse/tree.py`）：

1. 用正则识别文首「目录」块：从「目录」标题起到下一个「真正正文标题」（带 `第X章` / `^\d+(\.\d+)*\s` / ATX heading 且不像 TOC 点线）之前，标为 TOC 区，**不生成切分节点**；可解析 TOC 行得到 `(numbering, title)` 词典。
2. 正文标题候选：`^(#{1,6})\s+(.+)$`；剥离标题中的序号得到 `numbering` + 纯标题。
3. 按出现顺序建栈生成树；`start_offset` = 标题行结束位置；同级下一标题前为 `end_offset`；后序填 `subtree_end`；`self_start` = 标题行起始。
4. 若 TOC 词典与正文标题冲突：以正文顺序为准，仅用 TOC 纠正 `level`（当正文 level 模糊时）。
5. 无任何正文标题：返回单节点 `全文`，`start_offset=0`，`end_offset=len(md)`，`source=heading`，并在返回结构中带 `warnings=["no_headings"]`（函数可返回 `(tree, warnings)`）。

导出：

```python
def build_document_tree(markdown: str) -> dict:
    """Return {"nodes": [root...,], "warnings": [...]} with nested children OR flat+parent_id.
    Prefer nested children for API; keep flatten_nodes helper for tests.
    """

def flatten_nodes(tree: dict) -> list[dict]:
    ...
```

节点字段与 spec / `TreeNodeOut` 一致。

- [ ] **Step 5: Run tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_parse_tree.py -v`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/parse backend/tests/test_parse_tree.py backend/tests/fixtures/sample_with_toc.md
git commit -m "feat: add TOC-aware document tree builder"
```

---

### Task 5: Chunk + convert (DOCX/PDF) + extract tables/images

**Files:**
- Create: `backend/app/services/parse/chunk.py`
- Create: `backend/app/services/parse/convert.py`
- Create: `backend/app/services/parse/extract.py`
- Create: `backend/app/services/parse/pipeline.py`
- Create: `backend/tests/test_parse_extract_table.py`
- Create: `backend/tests/test_parse_pipeline.py`
- Modify: `backend/requirements.txt` — add `pymupdf>=1.24,<2`

- [ ] **Step 1: Add dependency**

```bash
.venv/bin/pip install 'pymupdf>=1.24,<2'
```

Append to `backend/requirements.txt`:

```text
pymupdf>=1.24,<2
```

- [ ] **Step 2: Chunk helper + test in pipeline later**

`chunk.py`:

```python
def chunk_from_tree(markdown: str, tree: dict, *, max_chars: int = 4000) -> list[dict]:
    """Leaf nodes → chunks; split long leaves by paragraphs."""
```

每块含 `chunk_id`, `node_id`, `title_path`, `start`, `end`, `text`。

- [ ] **Step 3: DOCX table extract test**

`test_parse_extract_table.py`：用 `python-docx` 在 tmp 生成含合并单元格的表格（`cell.merge`），调用 `extract_tables_from_docx(path, out_dir)`，断言生成 `tbl_001.html` 且含 `rowspan` 或 `colspan`。

再测：monkeypatch 内部函数抛错 → 返回 `warnings` 非空且不抛异常（供 partial）。

- [ ] **Step 4: Implement convert / extract / pipeline**

`convert.py`：

- `convert_docx_to_markdown(path, image_dir) -> str`：遍历段落；`style.name` 含 Heading → `#` 层级；普通段落原文；图片保存到 `image_dir` 并插入 `![](...)`。
- `convert_pdf_to_markdown(path, image_dir) -> str`：PyMuPDF 按页取文本；字号显著更大的行标为候选标题（前缀 `#`/`##`）；嵌入图片导出。

`extract.py`：

- 规范化 markdown 内图片路径到 `../image/{file_id}/...`
- `extract_tables_from_docx` / `extract_tables_from_pdf` → HTML+CSV；失败收集 warnings；在 md 中对应位置插入 `<!-- table:tbl_XXX -->`（DOCX 可在原 table 段落位置替换；PDF 可在文末附录表列表 + 占位）。

`pipeline.py`：

```python
async def run_parse_pipeline(file_id: str, task_id: str, stored_path: str) -> dict:
    """
    Returns {
      "status": "succeeded"|"partial"|"failed",
      "md_path", "tree_path", "chunks_path",
      "error": str|None, "warnings": list[str]
    }
    """
```

同步文件 IO 可用 `asyncio.to_thread` 包一层。阶段顺序严格按 spec；`build_document_tree` 用 convert 后的 md；写 `json/{file_id}.*.json` 与更新路径。

- [ ] **Step 5: Pipeline integration test**

`test_parse_pipeline.py`：对简单 docx（标题+段落+表）跑 `run_parse_pipeline`，断言 md/tree/chunks 存在，tree 含标题，status in `{succeeded, partial}`。

- [ ] **Step 6: Commit**

```bash
git add backend/requirements.txt backend/app/services/parse backend/tests/test_parse_extract_table.py backend/tests/test_parse_pipeline.py
git commit -m "feat: add document convert, extract, chunk pipeline"
```

---

### Task 6: Parse scheduler + recover + create/import kick

**Files:**
- Create: `backend/app/services/parse_scheduler.py`
- Modify: `backend/app/db.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/tasks.py`（`parse_scheduler.kick()`）
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_parse_pipeline.py` 或新建 `test_parse_scheduler.py`

- [ ] **Step 1: Implement scheduler**

`parse_scheduler.py` 模式对齐诊断 scheduler：

```python
_worker: asyncio.Task | None = None
_wake: asyncio.Event

async def kick() -> None: ...

async def _loop() -> None:
    while True:
        job = await _claim_next_queued()
        if job is None:
            await wait wake / timeout
            continue
        await _run_job(job)

async def recover_interrupted() -> None:
    # running → queued

def reset_for_tests() -> None: ...
```

`_run_job`：设 file `running`、job `running`；调用 `run_parse_pipeline`；写回路径与 `parse_status`；`artifact_refresh_index`；job `succeeded`/`failed`。

- [ ] **Step 2: `db.recover_interrupted_parse_jobs` + lifespan**

```python
async def recover_interrupted_parse_jobs() -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(ParseJob).where(ParseJob.status == "running").values(status="queued", stage="convert")
        )
        await session.execute(
            update(WorkspaceFile)
            .where(WorkspaceFile.parse_status == "running")
            .values(parse_status="pending")
        )
        await session.commit()
```

`main.py` lifespan：在 `recover_interrupted_tasks` 后调用 `recover_interrupted_parse_jobs` 与 `parse_scheduler.kick()`。

- [ ] **Step 3: Tests**

- 入队后 `kick` + 等待 file `parse_status in {succeeded, partial, failed}`
- `reset_for_tests` 在 conftest 的 setup/teardown 调用
- monkeypatch `UPLOAD_DIR` 时同步 patch `artifact.UPLOAD_DIR`、`workspace.UPLOAD_DIR`、`parse_scheduler` 所用路径

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/parse_scheduler.py backend/app/db.py backend/app/main.py backend/app/api/tasks.py backend/tests/conftest.py backend/tests/test_parse_scheduler.py
git commit -m "feat: add parse scheduler and recovery"
```

---

### Task 7: Workspace API

**Files:**
- Create: `backend/app/api/workspaces.py`
- Create: `backend/tests/test_workspaces_api.py`
- Modify: `backend/app/main.py` — `include_router`
- Modify: `backend/app/services/workspace.py` — `import_file`, `reparse`, `get_tree`, `get_content`

- [ ] **Step 1: Failing API tests**

覆盖：

1. `POST /api/tasks` 后 `GET /api/workspaces` 含该 task；`GET /api/workspaces/{id}` 含 2 个文件  
2. `POST /api/workspaces/{id}/files`（docx + label）→ 201，多一个文件且 `parse_status=pending`  
3. 解析完成后（可 monkeypatch pipeline 为快速写假 md/tree）`GET .../tree` 200；`GET .../content?node_id=` 返回切片  
4. `POST .../reparse` 在 failed 上可用  
5. 无效 node_id → 404  

- [ ] **Step 2: Implement router**

`workspaces.py` 前缀 `/api/workspaces`，实现 spec §6.4 全部端点。

`import_file`：

- 校验 label 非空；扩展名 in `ALLOWED_EXTENSIONS` → `document/` + enqueue；否则 → `other/` + `parse_status=skipped`
- 大小限制复用 `files` 逻辑或抽公共函数

`get_content`：读 `md_path` + tree 找 node，用 `self_start:subtree_end` 或 `start_offset:end_offset`（API 约定：**右侧展示用 `start_offset:end_offset` 本节正文**；若需含子树可加 query `mode=section|subtree`，默认 `section`）

- [ ] **Step 3: Pass tests + commit**

```bash
git add backend/app/api/workspaces.py backend/app/services/workspace.py backend/app/main.py backend/tests/test_workspaces_api.py
git commit -m "feat: add workspace REST API"
```

---

### Task 8: Sync reports into Artifact `report/`

**Files:**
- Modify: `backend/app/services/report.py` 和/或 `interpret_report.py`
- Modify: `backend/tests/test_report.py` / `test_interpret_report.py`

- [ ] **Step 1: After writing report files, copy/symlink into `uploads/{task_id}/report/`**

在 `save_report` / `save_interpret_report` 成功后：

```python
from app.services import artifact
import shutil

def sync_to_artifact_report(task_id: str, *paths: Path) -> None:
    dest_dir = artifact.ensure_artifact_dirs(task_id) / "report"
    for p in paths:
        if p and Path(p).is_file():
            shutil.copy2(p, dest_dir / Path(p).name)
```

- [ ] **Step 2: Test copy exists under artifact report/**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: sync diagnosis reports into artifact report/"
```

---

### Task 9: Frontend — API helpers + list page + nav

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.jsx`
- Create: `frontend/src/pages/WorkspaceListPage.jsx`
- Modify: `frontend/src/pages/TaskListPage.jsx`（加「工作区」链接）
- Modify: `frontend/src/pages/admin/AdminLayout.jsx`（若有主导航则加链接）

- [ ] **Step 1: API helpers**

```js
export function listWorkspaces() {
  return request('/api/workspaces')
}
export function getWorkspace(taskId) {
  return request(`/api/workspaces/${taskId}`)
}
export function importWorkspaceFile(taskId, formData) {
  return request(`/api/workspaces/${taskId}/files`, { method: 'POST', body: formData })
}
export function getWorkspaceTree(taskId, fileId) {
  return request(`/api/workspaces/${taskId}/files/${fileId}/tree`)
}
export function getWorkspaceContent(taskId, fileId, nodeId) {
  return request(`/api/workspaces/${taskId}/files/${fileId}/content?node_id=${encodeURIComponent(nodeId)}`)
}
export function reparseWorkspaceFile(taskId, fileId) {
  return request(`/api/workspaces/${taskId}/files/${fileId}/reparse`, { method: 'POST' })
}
export function workspaceFileDownloadUrl(taskId, fileId) {
  return `/api/workspaces/${taskId}/files/${fileId}/download`
}
```

- [ ] **Step 2: `WorkspaceListPage`**

表格/卡片：task_id、招标/标书名、文件数、解析汇总、创建时间；点击 → `/workspaces/:taskId`。  
Header 链回「诊断」与「管理后台」。

- [ ] **Step 3: Routes**

```jsx
import WorkspaceListPage from './pages/WorkspaceListPage'
import WorkspaceDetailPage from './pages/WorkspaceDetailPage'

<Route path="/workspaces" element={<WorkspaceListPage />} />
<Route path="/workspaces/:taskId" element={<WorkspaceDetailPage />} />
```

（Detail 可先占位，Task 10 填满）

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat: add workspace list page and routes"
```

---

### Task 10: Frontend — detail page (files + tree + content)

**Files:**
- Create: `frontend/src/pages/WorkspaceDetailPage.jsx`
- Create: `frontend/src/components/DocumentTree.jsx`
- Create: `frontend/src/components/ImportFileModal.jsx`
- Modify: `frontend/src/pages/TaskDetailPage.jsx` — 「打开工作区」
- Modify: `frontend/src/App.css`

- [ ] **Step 1: `DocumentTree`**

Props: `nodes`（嵌套 children）、`selectedId`、`onSelect(node)`。  
递归渲染可折叠列表；点击标题调用 `onSelect`。

- [ ] **Step 2: `ImportFileModal`**

字段：文件 input + label 文本；提交 `FormData`：`file`, `label`。

- [ ] **Step 3: `WorkspaceDetailPage`**

- 加载 `getWorkspace`；若有 pending/running，2s 轮询  
- 上方文件表：label、filename、ext、parse_status、下载、重试（failed/partial）  
- 点击行（succeeded/partial）：拉 tree；默认选中第一个节点；右侧 `MarkdownPreview` 显示 content  
- 导入按钮打开 modal  

- [ ] **Step 4: Task detail link**

```jsx
<Link to={`/workspaces/${id}`}>打开工作区</Link>
```

- [ ] **Step 5: CSS**

`.workspace-reader { display: grid; grid-template-columns: 280px 1fr; gap: 1rem; }` 等，沿用现有变量/按钮样式，不新开设计体系。

- [ ] **Step 6: 手工验收（按 spec §8.2）+ commit**

```bash
git add frontend/src
git commit -m "feat: workspace detail with document tree reader"
```

---

### Task 11: End-to-end hardening + README

**Files:**
- Modify: `README.md` — 增加工作区路由与验收项
- Modify: spec 状态为「已批准」（若尚未）
- 跑全量测试

- [ ] **Step 1: Full pytest**

Run: `cd backend && ../.venv/bin/python -m pytest -v`  
Expected: PASS

- [ ] **Step 2: README 表格增加**

| 工作区列表 | `/workspaces` |
| 工作区详情 | `/workspaces/:taskId` |

验收补充：导入解析、树浏览、重试。

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/specs/2026-07-16-workspace-management-design.md
git commit -m "docs: document workspace management usage"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|---|---|
| Artifact 目录结构 + index.md | 2, 3, 5 |
| WorkspaceFile / ParseJob | 1 |
| 创建任务自动注册+解析 | 3, 6 |
| 自由标签导入 | 7, 10 |
| convert / image / table / tree / chunks | 4, 5 |
| TOC 不切入章节 | 4 |
| partial / 重试 | 5, 6, 7 |
| `/workspaces` UI + 左树右文 | 9, 10 |
| report/ 同步 | 8 |
| 不做检索 | —（刻意不做） |
| 进程重启 ParseJob 重入队 | 6 |

---

## Self-review notes

- 无 TBD；API 字段与 models 对齐 `parse_status` / `stage=write_index`
- 内容切片默认 `section`（`start_offset:end_offset`），与树节点 `subtree_end` 并存
- `test_tasks` 若依赖旧路径，Task 3 必须同步改断言
- PDF 表格质量依赖 PyMuPDF；单表失败走 partial，不阻塞整篇
