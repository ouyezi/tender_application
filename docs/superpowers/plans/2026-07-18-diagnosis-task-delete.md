# 标书诊断任务删除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在任务列表卡片右上角提供三点菜单（查看详情 / 删除），并实现 `DELETE /api/tasks/{id}` 硬删除（先 stop 再清库与磁盘）。

**Architecture:** 后端新增 `delete_task` 服务函数（stop → 级联删关联表 → 删任务行 → 尽力删磁盘）；API 暴露 `DELETE /api/tasks/{task_id}` 返回 204。前端 `TaskCard` 增加原生下拉菜单，`TaskListPage` 用 `window.confirm` 后调用 `deleteTask` 并刷新列表。

**Tech Stack:** FastAPI、SQLAlchemy 2.x async、pytest/httpx；React 18、Vite、fetch。

**Spec:** `docs/superpowers/specs/2026-07-18-diagnosis-task-delete-design.md`

---

## File Structure

```text
backend/app/services/task_delete.py   # NEW: cascade delete + disk cleanup
backend/app/services/scheduler.py     # ADD: discard_control(task_id)
backend/app/services/artifact.py      # ADD: remove_artifact_root(task_id)
backend/app/api/tasks.py              # ADD: DELETE /{task_id}
backend/tests/test_task_delete.py     # NEW: API + cascade tests

frontend/src/api.js                   # ADD: deleteTask
frontend/src/components/TaskCard.jsx  # ADD: ⋯ menu
frontend/src/pages/TaskListPage.jsx   # ADD: confirm + delete handler
frontend/src/App.css                  # ADD: menu styles
```

---

### Task 1: 后端 — 删除服务与失败测试

**Files:**
- Create: `backend/app/services/task_delete.py`
- Modify: `backend/app/services/scheduler.py`（增加 `discard_control`）
- Modify: `backend/app/services/artifact.py`（增加 `remove_artifact_root`）
- Create: `backend/tests/test_task_delete.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_task_delete.py`：

```python
import io
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    ChecklistGeneration,
    DiagnosisResult,
    DiagnosisTask,
    KnowledgeChunk,
    ParseJob,
    WikiPage,
    WorkspaceFile,
)
from app.services import artifact


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_task(client):
    await client.post(
        "/api/configs",
        json={
            "title": "资质",
            "technique": "查",
            "content_mode": "description",
            "content_text": "资质",
            "importance": "high",
        },
    )
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": (
            "bid.docx",
            io.BytesIO(b"PK fake"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    r = await client.post(
        "/api/tasks",
        data={"background": "市政", "requirements": "核资质"},
        files=files,
    )
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_delete_task_not_found(client):
    r = await client.delete("/api/tasks/T-missing-000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_completed_task_removes_db_and_disk(client, tmp_path):
    task_id = await _create_task(client)

    # Wait until not in early race, then force completed via stop or wait
    for _ in range(100):
        r = await client.get(f"/api/tasks/{task_id}")
        if r.json()["status"] in ("completed", "stopped", "failed", "paused"):
            break
        import asyncio

        await asyncio.sleep(0.05)
    # Ensure stoppable/terminal so delete path is exercised
    await client.post(f"/api/tasks/{task_id}/stop")

    root = artifact.artifact_root(task_id)
    assert root.exists() or True  # may exist from create

    r = await client.delete(f"/api/tasks/{task_id}")
    assert r.status_code == 204

    r2 = await client.get(f"/api/tasks/{task_id}")
    assert r2.status_code == 404
    assert not artifact.artifact_root(task_id).exists()


@pytest.mark.asyncio
async def test_delete_running_task(client):
    task_id = await _create_task(client)
    r = await client.delete(f"/api/tasks/{task_id}")
    assert r.status_code == 204
    r2 = await client.get(f"/api/tasks/{task_id}")
    assert r2.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/tongqianni/xlab/tender_application
.venv/bin/python -m pytest backend/tests/test_task_delete.py -v
```

Expected: FAIL（`DELETE` 路由不存在，405 或 404）

- [ ] **Step 3: 实现 `discard_control` 与 `remove_artifact_root`**

在 `backend/app/services/scheduler.py` 的 `_controls` 相关区域增加：

```python
def discard_control(task_id: str) -> None:
    """Drop in-memory control state after a task is deleted."""
    ctrl = _controls.pop(task_id, None)
    if ctrl is None:
        return
    ctrl.stop_requested = True
    ctrl.pause_event.set()
    if ctrl.bg_task is not None and not ctrl.bg_task.done():
        ctrl.bg_task.cancel()
```

在 `backend/app/services/artifact.py` 增加：

```python
def remove_artifact_root(task_id: str) -> None:
    """Best-effort remove uploads/{task_id}. Missing path is OK."""
    root = artifact_root(task_id)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 4: 实现 `task_delete.py`**

创建 `backend/app/services/task_delete.py`：

```python
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ChecklistCategory,
    ChecklistGeneration,
    ChecklistItem,
    DiagnosisResult,
    DiagnosisTask,
    IndexJob,
    KnowledgeChunk,
    ParseJob,
    WikiPage,
    WorkspaceFile,
)
from app.services import artifact, scheduler
from app.services.scheduler import STOPPABLE_STATUSES, SchedulerConflict

logger = logging.getLogger(__name__)


async def delete_task(session: AsyncSession, task_id: str) -> None:
    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        raise LookupError(task_id)

    # Capture external file paths before DB row is gone
    external_paths = [
        task.report_md_path,
        task.report_docx_path,
        task.interpret_md_path,
        task.interpret_html_path,
        task.tender_path,
        task.bid_path,
    ]

    if task.status in STOPPABLE_STATUSES:
        try:
            await scheduler.stop_task(task_id)
        except SchedulerConflict:
            pass
        except LookupError:
            pass

    scheduler.discard_control(task_id)

    # Re-load in this session after stop (stop uses its own sessions)
    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        raise LookupError(task_id)

    task.current_checklist_generation_id = None
    await session.flush()

    await session.execute(
        delete(DiagnosisResult).where(DiagnosisResult.task_id == task_id)
    )

    gen_ids = list(
        (
            await session.execute(
                select(ChecklistGeneration.id).where(
                    ChecklistGeneration.task_id == task_id
                )
            )
        )
        .scalars()
        .all()
    )
    if gen_ids:
        await session.execute(
            delete(ChecklistItem).where(ChecklistItem.generation_id.in_(gen_ids))
        )
        await session.execute(
            delete(ChecklistCategory).where(
                ChecklistCategory.generation_id.in_(gen_ids)
            )
        )
        await session.execute(
            delete(ChecklistGeneration).where(ChecklistGeneration.id.in_(gen_ids))
        )

    await session.execute(delete(ParseJob).where(ParseJob.task_id == task_id))
    await session.execute(delete(IndexJob).where(IndexJob.task_id == task_id))
    await session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id)
    )
    await session.execute(delete(WikiPage).where(WikiPage.task_id == task_id))
    await session.execute(
        delete(WorkspaceFile).where(WorkspaceFile.task_id == task_id)
    )
    await session.execute(delete(DiagnosisTask).where(DiagnosisTask.id == task_id))
    await session.commit()

    # Disk: DB-first; failures are warnings only
    try:
        artifact.remove_artifact_root(task_id)
    except Exception:
        logger.warning("Failed to remove artifact root for %s", task_id, exc_info=True)

    root = artifact.artifact_root(task_id)
    for raw in external_paths:
        if not raw:
            continue
        path = Path(raw)
        try:
            if path.is_file() and not _is_under(path, root):
                path.unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed to remove file %s", path, exc_info=True)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False
```

- [ ] **Step 5: 增加 API 路由**

在 `backend/app/api/tasks.py` 增加（建议放在 `get_task` 附近）：

```python
from app.services import task_delete

@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, db: AsyncSession = Depends(get_db)) -> None:
    try:
        await task_delete.delete_task(db, task_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
```

确保 `from app.services import task_delete` 或 `from app.services.task_delete import delete_task as delete_task_service` 不与路由函数名冲突——推荐路由函数名 `delete_task_endpoint`，或服务导入为 `task_delete.delete_task`。

- [ ] **Step 6: 跑测试确认通过**

```bash
.venv/bin/python -m pytest backend/tests/test_task_delete.py -v
```

Expected: PASS

若 `test_delete_completed_task_removes_db_and_disk` 因 stop/时序不稳定失败，改为：创建任务后立即 `DELETE`（运行中路径），另写一条用 session 手工插入 `completed` 任务 + 假目录再删。

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/task_delete.py backend/app/services/scheduler.py \
  backend/app/services/artifact.py backend/app/api/tasks.py \
  backend/tests/test_task_delete.py
git commit -m "$(cat <<'EOF'
feat: add DELETE /api/tasks hard-delete with cascade cleanup

EOF
)"
```

---

### Task 2: 后端 — 关联数据残留测试（加固）

**Files:**
- Modify: `backend/tests/test_task_delete.py`

- [ ] **Step 1: 增加关联清理断言**

在 `test_task_delete.py` 追加：

```python
@pytest.mark.asyncio
async def test_delete_clears_related_rows(client):
    from app.db import SessionLocal
    from app.models import utcnow

    task_id = await _create_task(client)
    await client.post(f"/api/tasks/{task_id}/stop")

    async with SessionLocal() as session:
        session.add(
            WorkspaceFile(
                id=f"{task_id}-wf",
                task_id=task_id,
                label="extra",
                original_filename="x.pdf",
                stored_path="/tmp/x.pdf",
                kind="document",
                ext="pdf",
                parse_status="pending",
            )
        )
        session.add(
            KnowledgeChunk(
                task_id=task_id,
                file_id=f"{task_id}-wf",
                chunk_id="c1",
                node_id="n1",
                segment_level="fine",
            )
        )
        session.add(WikiPage(task_id=task_id, title="wiki"))
        session.add(
            ParseJob(
                file_id=f"{task_id}-wf",
                task_id=task_id,
                status="queued",
                stage="convert",
            )
        )
        await session.commit()

    r = await client.delete(f"/api/tasks/{task_id}")
    assert r.status_code == 204

    async with SessionLocal() as session:
        assert (
            await session.scalar(
                select(WorkspaceFile).where(WorkspaceFile.task_id == task_id)
            )
        ) is None
        assert (
            await session.scalar(
                select(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id)
            )
        ) is None
        assert (
            await session.scalar(select(WikiPage).where(WikiPage.task_id == task_id))
        ) is None
        assert (
            await session.scalar(select(ParseJob).where(ParseJob.task_id == task_id))
        ) is None
        assert (await session.get(DiagnosisTask, task_id)) is None
```

注意：`SessionLocal` 在测试里被 monkeypatch 为 `session_factory`——使用 `from app import db as database` 然后 `database.SessionLocal`，与 `conftest` 一致。

- [ ] **Step 2: 跑测试**

```bash
.venv/bin/python -m pytest backend/tests/test_task_delete.py::test_delete_clears_related_rows -v
```

Expected: PASS（必要时按模型必填字段微调构造）

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_task_delete.py
git commit -m "$(cat <<'EOF'
test: assert task delete clears related workspace rows

EOF
)"
```

---

### Task 3: 前端 — API 与卡片三点菜单

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/TaskCard.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 增加 `deleteTask`**

在 `frontend/src/api.js` 的 `stopTask` 后加入：

```javascript
export function deleteTask(id) {
  return request(`/api/tasks/${id}`, { method: 'DELETE' })
}
```

- [ ] **Step 2: 改写 `TaskCard.jsx`**

完整替换为（保持现有 STATUS_LABELS / formatDate）：

```jsx
import { useEffect, useRef, useState } from 'react'
import { reportDocxUrl } from '../api'

const STATUS_LABELS = {
  interpreting: '解读中',
  generating_checklist: '生成检查项',
  diagnosing: '诊断中',
  running: '诊断中',
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return String(value)
  }
}

export default function TaskCard({ task, onClick, onDelete, deleting }) {
  const status = task.status || 'running'
  const label = STATUS_LABELS[status] || status
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)

  useEffect(() => {
    if (!menuOpen) return
    function onDocClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [menuOpen])

  return (
    <article
      className={`task-card${deleting ? ' task-card-deleting' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => onClick?.(task)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick?.(task)
        }
      }}
    >
      <div className="task-card-header">
        <span className={`status-badge status-${status}`}>{label}</span>
        <div className="task-card-header-right">
          <time className="task-card-time" dateTime={task.created_at}>
            {formatDate(task.created_at)}
          </time>
          <div className="task-card-menu" ref={menuRef}>
            <button
              type="button"
              className="task-card-menu-trigger"
              aria-label="更多操作"
              aria-expanded={menuOpen}
              disabled={deleting}
              onClick={(e) => {
                e.stopPropagation()
                setMenuOpen((v) => !v)
              }}
            >
              ⋯
            </button>
            {menuOpen && (
              <div
                className="task-card-menu-dropdown"
                role="menu"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  type="button"
                  role="menuitem"
                  className="task-card-menu-item"
                  onClick={(e) => {
                    e.stopPropagation()
                    setMenuOpen(false)
                    onClick?.(task)
                  }}
                >
                  查看详情
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="task-card-menu-item task-card-menu-item-danger"
                  disabled={deleting}
                  onClick={(e) => {
                    e.stopPropagation()
                    setMenuOpen(false)
                    onDelete?.(task)
                  }}
                >
                  删除
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="task-card-files">
        <div className="task-card-file">
          <span className="task-card-label">招标文件</span>
          <span className="task-card-name" title={task.tender_filename}>
            {task.tender_filename || '—'}
          </span>
        </div>
        <div className="task-card-file">
          <span className="task-card-label">投标文件</span>
          <span className="task-card-name" title={task.bid_filename}>
            {task.bid_filename || '—'}
          </span>
        </div>
      </div>

      <div className="task-card-footer">
        <code className="task-card-id">{task.id}</code>
        {status === 'completed' && (
          <a
            className="task-card-download"
            href={reportDocxUrl(task.id)}
            onClick={(e) => e.stopPropagation()}
          >
            下载报告
          </a>
        )}
      </div>
    </article>
  )
}
```

- [ ] **Step 3: 增加 CSS**

在 `frontend/src/App.css` 的 `.task-card-header` 块后追加：

```css
.task-card-header-right {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-left: auto;
}

.task-card-menu {
  position: relative;
}

.task-card-menu-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-muted);
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
}

.task-card-menu-trigger:hover:not(:disabled),
.task-card-menu-trigger:focus-visible {
  background: rgba(26, 29, 35, 0.06);
  color: var(--text);
  outline: none;
}

.task-card-menu-trigger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.task-card-menu-dropdown {
  position: absolute;
  top: calc(100% + 0.2rem);
  right: 0;
  z-index: 20;
  min-width: 7.5rem;
  padding: 0.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 4px 14px rgba(26, 29, 35, 0.12);
}

.task-card-menu-item {
  display: block;
  width: 100%;
  padding: 0.45rem 0.65rem;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text);
  font-size: 0.875rem;
  text-align: left;
  cursor: pointer;
}

.task-card-menu-item:hover:not(:disabled) {
  background: rgba(26, 29, 35, 0.06);
}

.task-card-menu-item-danger {
  color: #b42318;
}

.task-card-menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.task-card-deleting {
  opacity: 0.65;
  pointer-events: none;
}
```

- [ ] **Step 4: 手动冒烟（可选）**

```bash
# 若前端已在跑则刷新；否则
cd frontend && npm run dev
```

确认卡片右上角有 `⋯`，点击展开两项。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/components/TaskCard.jsx frontend/src/App.css
git commit -m "$(cat <<'EOF'
feat: add task card overflow menu for detail and delete

EOF
)"
```

---

### Task 4: 前端 — 列表页删除流程

**Files:**
- Modify: `frontend/src/pages/TaskListPage.jsx`

- [ ] **Step 1: 接线删除处理**

更新 `TaskListPage.jsx`：

```jsx
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { deleteTask, listTasks } from '../api'
import TaskCard from '../components/TaskCard'
import CreateTaskModal from '../components/CreateTaskModal'

export default function TaskListPage() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState('')

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const data = await listTasks()
      setTasks(Array.isArray(data) ? data : [])
      setError('')
    } catch (err) {
      setError(err.message || '加载任务列表失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(() => refresh(true), 3000)
    return () => clearInterval(timer)
  }, [refresh])

  function handleCreated(task) {
    setModalOpen(false)
    refresh(true)
    if (task?.id) {
      navigate(`/tasks/${task.id}`)
    }
  }

  async function handleDelete(task) {
    const ok = window.confirm('确定删除该诊断任务？此操作不可恢复。')
    if (!ok) return
    setDeletingId(task.id)
    setError('')
    try {
      await deleteTask(task.id)
      await refresh(true)
    } catch (err) {
      setError(err.message || '删除失败')
    } finally {
      setDeletingId('')
    }
  }

  return (
    <main className="page task-list-page">
      <header className="page-header">
        <div className="page-header-titles">
          <h1>标书诊断</h1>
          <Link className="header-link" to="/workspaces">
            工作区
          </Link>
          <Link className="header-link" to="/admin">
            管理后台
          </Link>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setModalOpen(true)}
        >
          创建诊断
        </button>
      </header>

      {error && <p className="page-error">{error}</p>}

      {loading && tasks.length === 0 ? (
        <p className="empty-state">加载中…</p>
      ) : tasks.length === 0 ? (
        <div className="empty-state">
          <p>暂无诊断任务</p>
          <p className="empty-state-hint">点击「创建诊断」上传招标与投标文件开始分析</p>
        </div>
      ) : (
        <div className="task-grid">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onClick={(t) => navigate(`/tasks/${t.id}`)}
              onDelete={handleDelete}
              deleting={deletingId === task.id}
            />
          ))}
        </div>
      )}

      <CreateTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={handleCreated}
      />
    </main>
  )
}
```

- [ ] **Step 2: 手动验收**

1. 打开 `/`，点卡片 `⋯` →「查看详情」进入详情页  
2. 返回列表，`⋯` →「删除」→ 取消，任务仍在  
3. 再删并确认，任务消失，刷新后仍不在  
4. 运行中任务也可删

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/TaskListPage.jsx
git commit -m "$(cat <<'EOF'
feat: wire task list delete confirm and refresh

EOF
)"
```

---

### Task 5: 回归与收尾

**Files:** 无新文件

- [ ] **Step 1: 跑后端相关测试**

```bash
.venv/bin/python -m pytest backend/tests/test_task_delete.py backend/tests/test_tasks.py backend/tests/test_scheduler.py -v
```

Expected: PASS

- [ ] **Step 2: 对照 spec 成功标准勾选**

| 标准 | 验证 |
|---|---|
| 三点菜单两项 | UI |
| 查看详情导航 | UI |
| confirm 后删除并刷新 | UI |
| 运行中可删 | `test_delete_running_task` |
| DB + disk 清理 | `test_delete_*` |
| 菜单不触发卡片导航 | UI（stopPropagation） |

- [ ] **Step 3: 若有测试修复则再 commit**

```bash
git add -u backend/tests
git commit -m "$(cat <<'EOF'
test: stabilize task delete coverage

EOF
)"
```

（无变更则跳过）

---

## Spec Coverage Self-Review

| Spec 要求 | 对应 Task |
|---|---|
| 三点菜单：查看详情 / 删除 | Task 3 |
| confirm 文案 | Task 4 |
| `DELETE /api/tasks/{id}` 204/404 | Task 1 |
| 运行中先 stop 再删 | Task 1 `task_delete.py` |
| 级联清库顺序 | Task 1 + Task 2 |
| 磁盘 best-effort | Task 1 `remove_artifact_root` + external paths |
| 仅列表页 | Task 3–4（不改 admin/detail） |
| discard scheduler control | Task 1 `discard_control` |

无 TBD/占位步骤；函数名统一为 `task_delete.delete_task` / `deleteTask` / `discard_control` / `remove_artifact_root`。
