# Offline 诊断跳过与真实解读接通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将招标解读切换为 Agent OS 真实输出，并为检查项增加 `diagnosis_mode`，使 offline（打印/装订等）项跳过文件检索与批诊断、结果记为 `manual_required`。

**Architecture:** 解读复用 main 已有 `AgentOSClient`，从分支 `feat/agent-os-tender-interpretation` 移植 `TenderContentProvider` 与 `AgentOSInterpretationAgent` 并接线 scheduler。offline 在 `_run_diagnosis_phase` 内按项分流直写结果，不引入独立 Router。检查项生成 Agent 配置同步增加 `diagnosis_mode` 字段。

**Tech Stack:** FastAPI、SQLAlchemy、asyncio、httpx、pytest-asyncio、React（前端标签）。

**Spec:** `docs/superpowers/specs/2026-07-19-offline-diagnosis-and-real-interpretation-design.md`

**Note:** 解读细节契约以 `docs/superpowers/specs/2026-07-17-agent-os-tender-interpretation-design.md` 为准。main 上 `AgentOSClient` 已存在——**禁止**按旧解读 plan 重建客户端；分支文件移植时适配 `AgentOSClient(settings=...)` 关键字构造。

---

## File Structure

```text
backend/app/
  config.py                              # 移除或旁路 INTERPRETATION_AGENT mock 默认
  engine/
    base.py                              # InterpretationAgent 签名；ChecklistItemDraft.diagnosis_mode；BatchItemResult 注释
    interpretation_mock.py               # 同步新签名（仅测试残留）
    interpretation_agent_os.py           # NEW：从分支移植
    checklist_agent_os.py                # 解析 diagnosis_mode
    checklist_merge.py                   # 透传 diagnosis_mode
  models.py                              # ChecklistItem.diagnosis_mode
  schemas.py                             # ChecklistItemOut.diagnosis_mode
  services/
    agent_os.py                          # AgentOSSettings 增加 parse_wait_timeout_seconds
    tender_content.py                    # NEW：从分支移植
    scheduler.py                         # 真解读接线 + offline 分流
    checklist_service.py                 # 校验/落库/get_report 透传 diagnosis_mode
    checklist_context.py                 # SYSTEM_INSTRUCTIONS 增加 diagnosis_mode 规则
    report.py                            # COMPLIANCE_LABELS.manual_required
  main.py                                # 若分支有 shutdown_pending_operations，按需接入

docs/agents_config/
  tender_checklist_generator.json        # outputSchema + systemPrompt 增加 diagnosis_mode

frontend/src/components/
  ResultTable.jsx                        # manual_required 标签
  ChecklistReport.jsx                    # offline「线下核验」标签

backend/tests/
  test_tender_content.py                 # 从分支移植
  test_interpretation_agent_os.py        # 从分支移植
  test_interpretation_agent.py           # 更新 Mock 签名
  test_scheduler.py                      # 解读 stub + offline 分流用例
  test_checklist_agent_os.py             # diagnosis_mode 解析
  test_checklist_service.py / api        # 透传与默认
  test_report.py                         # manual_required 文案
  conftest.py                            # stub 解读依赖
  fake_checklist_invoke.py               # fake 项可带 diagnosis_mode
```

**执行顺序建议：** Phase A（Task 1–4）可单独合入并绿测；Phase B（Task 5–10）依赖检查项链路；Phase C（Task 11–12）为 Agent 配置与收尾。

---

## Phase A — 真实解读接通

### Task 1: 扩展 AgentOSSettings（解析等待超时）

**Files:**
- Modify: `backend/app/services/agent_os.py`
- Modify: `backend/tests/test_agent_os_client.py`（若无对应用例则追加）
- Create/Update: `config.local.json.example` 或仓库已有 `config.example.json`（增加 `tenderInterpretation.parseWaitTimeoutSeconds` 示例，无凭据）

- [ ] **Step 1: Write failing test for parse wait timeout load**

在 `backend/tests/test_agent_os_client.py` 追加：

```python
def test_load_settings_parse_wait_timeout(tmp_path, monkeypatch):
    import json
    from app.services import agent_os

    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps(
            {
                "agentOs": {"baseUrl": "http://localhost:8000"},
                "tenderInterpretation": {"parseWaitTimeoutSeconds": 600},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", cfg)
    monkeypatch.delenv("TENDER_PARSE_WAIT_TIMEOUT_SECONDS", raising=False)
    settings = agent_os.load_settings()
    assert settings.parse_wait_timeout_seconds == 600.0

    monkeypatch.setenv("TENDER_PARSE_WAIT_TIMEOUT_SECONDS", "100")
    settings = agent_os.load_settings()
    assert settings.parse_wait_timeout_seconds == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_parse_wait_timeout -v`

Expected: FAIL（`AgentOSSettings` 无 `parse_wait_timeout_seconds`）

- [ ] **Step 3: Implement settings field**

在 `AgentOSSettings` 增加：

```python
parse_wait_timeout_seconds: float = 1800.0
```

在 `load_settings()` 中读取：

```python
tender_interp = (
    local.get("tenderInterpretation")
    if isinstance(local.get("tenderInterpretation"), dict)
    else {}
)
# ...
parse_wait_timeout_seconds=_as_float(
    _env_or(
        tender_interp.get("parseWaitTimeoutSeconds"),
        "TENDER_PARSE_WAIT_TIMEOUT_SECONDS",
        1800,
    ),
    1800.0,
),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_parse_wait_timeout -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_os.py backend/tests/test_agent_os_client.py config.example.json
git commit -m "$(cat <<'EOF'
feat: load tender parse wait timeout for interpretation

EOF
)"
```

---

### Task 2: 移植 TenderContentProvider 与解读适配器

**Files:**
- Create: `backend/app/services/tender_content.py`（自分支）
- Create: `backend/app/engine/interpretation_agent_os.py`（自分支）
- Modify: `backend/app/engine/base.py`（InterpretationAgent 签名）
- Modify: `backend/app/engine/interpretation_mock.py`（同步签名）
- Create: `backend/tests/test_tender_content.py`（自分支）
- Create: `backend/tests/test_interpretation_agent_os.py`（自分支）
- Modify: `backend/tests/test_interpretation_agent.py`

- [ ] **Step 1: Update InterpretationAgent protocol (TDD via Mock test first)**

修改 `backend/app/engine/base.py`：

```python
class InterpretationAgent(Protocol):
    async def interpret(
        self,
        *,
        task_id: str,
        tender_text: str,
        background: str,
        requirements: str,
    ) -> InterpretationResult: ...
```

同步更新 `interpretation_mock.py`：忽略正文内容、保留可测延迟，签名改为 `tender_text` / `requirements`（可仍用 `task_id`+`background` 拼简单 Markdown，供本地残留；生产路径不再调用）。

- [ ] **Step 2: Copy provider + adapter + their tests from the feature branch**

在仓库根目录执行（保持文件内容与分支一致，随后做适配）：

```bash
git show feat/agent-os-tender-interpretation:backend/app/services/tender_content.py \
  > backend/app/services/tender_content.py
git show feat/agent-os-tender-interpretation:backend/app/engine/interpretation_agent_os.py \
  > backend/app/engine/interpretation_agent_os.py
git show feat/agent-os-tender-interpretation:backend/tests/test_tender_content.py \
  > backend/tests/test_tender_content.py
git show feat/agent-os-tender-interpretation:backend/tests/test_interpretation_agent_os.py \
  > backend/tests/test_interpretation_agent_os.py
```

适配检查清单：

1. `AgentOSInterpretationAgent` 构造接受 `client: AgentOSInvoker`（Protocol，含 `invoke_app`）——与分支一致即可。
2. 任何 `AgentOSClient(...)` 调用改为 `AgentOSClient(settings=settings)`（main 为 keyword-only）。
3. 超时取自 `load_settings().parse_wait_timeout_seconds`，不要依赖分支独有的 `agent_os_config.py`（main 无此文件则不要引入；把解析等待秒数放进 Task 1 的 settings）。
4. 若测试 import 了 `agent_os_config`，改为从 `app.services.agent_os` 读取。

- [ ] **Step 3: Run provider and adapter unit tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_tender_content.py \
  tests/test_interpretation_agent_os.py \
  tests/test_interpretation_agent.py -v
```

Expected: PASS（若 FAIL，按报错修 import / settings 适配，不要削弱断言）

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/tender_content.py \
  backend/app/engine/interpretation_agent_os.py \
  backend/app/engine/base.py \
  backend/app/engine/interpretation_mock.py \
  backend/tests/test_tender_content.py \
  backend/tests/test_interpretation_agent_os.py \
  backend/tests/test_interpretation_agent.py
git commit -m "$(cat <<'EOF'
feat: add tender content wait and Agent OS interpretation adapter

EOF
)"
```

---

### Task 3: Scheduler 接线真实解读 + conftest stub

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/app/config.py`（注释/移除 mock 默认误导；可不删常量以免大范围无关 diff）
- Modify: `backend/app/main.py`（若需在 shutdown 调用 `tender_content.shutdown_pending_operations`，对齐分支；无 lifespan 钩子则可暂缓，但测试 teardown 不得挂死）

- [ ] **Step 1: Add factory helpers and replace MockInterpretationAgent path**

在 `scheduler.py` 增加（贴近分支，适配 main Client）：

```python
from app.engine.interpretation_agent_os import AgentOSInterpretationAgent
from app.services.agent_os import AgentOSClient, load_settings
from app.services.tender_content import TenderContentProvider, TenderContentStopped


def _build_tender_content_provider() -> TenderContentProvider:
    settings = load_settings()
    return TenderContentProvider(timeout_seconds=settings.parse_wait_timeout_seconds)


def _build_interpretation_agent() -> AgentOSInterpretationAgent:
    return AgentOSInterpretationAgent(AgentOSClient())
```

将 `_run` 中解读段改为逻辑等价于：

1. 读取 `task.tender_file_id`、`background`、`requirements`
2. `tender_text = await provider.wait_for_markdown(task_id, tender_file_id, stop_requested=lambda: _should_stop(task_id))`
3. 捕获 `TenderContentStopped` → `_mark_stopped` 并 return
4. `interpret_result = await agent.interpret(task_id=..., tender_text=..., background=..., requirements=...)`
5. 停止检查后 `save_interpret_reports`，状态切 `generating_checklist`

删除对 `MockInterpretationAgent` 的生产调用。失败走现有 `_fail_task(..., failure_stage="interpreting")`（若函数名不同，用现有失败辅助）。

参考分支片段（勿原样粘贴构造器）：

```python
# feat/agent-os-tender-interpretation scheduler interpret block
# AgentOSClient() 无参；内部 load_settings()
# TenderContentProvider(timeout_seconds=settings.parse_wait_timeout_seconds)
```

- [ ] **Step 2: Stub interpretation in conftest so suite stays offline**

在 `backend/tests/conftest.py` 的 `client` fixture 中，在 yield 前增加：

```python
from app.engine.base import InterpretationResult

class _StubContentProvider:
    def __init__(self, *args, **kwargs):
        pass

    async def wait_for_markdown(self, task_id, file_id, *, stop_requested):
        del task_id, file_id, stop_requested
        return "# stub tender\n资格要求：须提交营业执照。\n"

class _StubInterpretationAgent:
    async def interpret(self, *, task_id, tender_text, background, requirements):
        del tender_text, background, requirements
        return InterpretationResult(
            markdown=f"# 招标文件解读报告\n\n**任务编号：** {task_id}\n\nstub interpret\n"
        )

monkeypatch.setattr(
    "app.services.scheduler._build_tender_content_provider",
    lambda: _StubContentProvider(),
)
monkeypatch.setattr(
    "app.services.scheduler._build_interpretation_agent",
    lambda: _StubInterpretationAgent(),
)
```

可移除仅服务于 Mock 解读 delay 的 monkeypatch（若不再引用）。

- [ ] **Step 3: Add/adjust scheduler tests**

确保 `test_scheduler_runs_to_completion` 仍 PASS，且 `interpret_md_path` 存在、解读内容含 stub 或非旧 Mock「（Mock）」固定句（stub 文案即可）。

追加用例（可放 `test_scheduler.py`）：

```python
@pytest.mark.asyncio
async def test_interpretation_failure_fails_task(client, monkeypatch):
    from app.services import scheduler

    class Boom:
        async def interpret(self, **kwargs):
            raise RuntimeError("interpret boom")

    monkeypatch.setattr(
        scheduler,
        "_build_interpretation_agent",
        lambda: Boom(),
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    status = await scheduler.wait_for_terminal(body["id"], timeout=10)
    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{body['id']}")).json()
    assert detail.get("failure_stage") in ("interpreting", None) or "interpret" in (
        detail.get("error_message") or ""
    ).lower()
```

（若项目 `failure_stage` 字段已稳定为 `interpreting`，断言该字段。）

- [ ] **Step 4: Run scheduler + interpretation related tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_scheduler.py \
  tests/test_interpretation_agent_os.py \
  tests/test_tender_content.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduler.py backend/tests/conftest.py \
  backend/tests/test_scheduler.py backend/app/config.py backend/app/main.py
git commit -m "$(cat <<'EOF'
feat: wire Agent OS interpretation into task scheduler

EOF
)"
```

---

## Phase B — diagnosis_mode 与 offline 分流

### Task 4: 模型 / Draft / Schema 增加 `diagnosis_mode`

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/engine/base.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_migrate_schema.py`（若有列迁移覆盖则追加）

- [ ] **Step 1: Write failing migration/model expectation**

若存在 `test_migrate_schema.py`，追加断言新库 `checklist_items` 含 `diagnosis_mode`。否则在下一步直接改模型并用 create_all 验证。

- [ ] **Step 2: Add column and draft field**

`ChecklistItem`：

```python
diagnosis_mode: Mapped[str] = mapped_column(
    String(16), nullable=False, default="file", server_default="file"
)
```

`ChecklistItemDraft` 增加：

```python
diagnosis_mode: str = "file"
```

`ChecklistItemOut` 增加：

```python
diagnosis_mode: str = "file"
```

`server_default="file"` 确保 `_migrate_sqlite_columns` 能 ALTER 已有表。

- [ ] **Step 3: Run migrate / model related tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_migrate_schema.py tests/test_db.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py backend/app/engine/base.py backend/app/schemas.py \
  backend/tests/test_migrate_schema.py
git commit -m "$(cat <<'EOF'
feat: add checklist diagnosis_mode column and draft field

EOF
)"
```

---

### Task 5: 解析 / 合并 / 落库透传 `diagnosis_mode`

**Files:**
- Modify: `backend/app/engine/checklist_agent_os.py`
- Modify: `backend/app/engine/checklist_merge.py`
- Modify: `backend/app/services/checklist_service.py`
- Modify: `backend/app/services/checklist_context.py`
- Modify: `backend/tests/fake_checklist_invoke.py`
- Modify: `backend/tests/test_checklist_agent_os.py`
- Modify: `backend/tests/test_checklist_service.py`（或 api 测试）

- [ ] **Step 1: Write failing parse tests**

在 `test_checklist_agent_os.py` 追加：

```python
def test_parse_checklist_payload_diagnosis_mode_defaults_and_normalizes():
    from app.engine.checklist_agent_os import parse_checklist_payload

    def _minimal(item_extra: dict):
        return {
            "schema_version": "1",
            "categories": [
                {
                    "id": "c1",
                    "name": "格式",
                    "description": "d",
                    "retrieval_query": "q",
                    "expected_locations": [],
                    "sort_order": 0,
                }
            ],
            "items": [
                {
                    "id": "i1",
                    "category_id": "c1",
                    "title": "装订要求",
                    "requirement": "胶装",
                    "technique": "线下",
                    "importance": "high",
                    "source_references": [
                        {
                            "section": "s",
                            "start": 0,
                            "end": 1,
                            "segment_index": 0,
                            "coordinate_space": "segment",
                        }
                    ],
                    "retrieval_hints": ["装订"],
                    "expected_evidence": ["装订"],
                    "compliance_rules": {
                        "satisfied": "a",
                        "violated": "b",
                        "cannot_satisfy": "c",
                        "insufficient_evidence": "d",
                    },
                    "consequence_rules": {"bid_unusable": "x"},
                    "admin_config_refs": [],
                    "sort_order": 0,
                    **item_extra,
                }
            ],
        }

    draft = parse_checklist_payload(_minimal({"diagnosis_mode": "offline"}))
    assert draft.items[0].diagnosis_mode == "offline"

    draft = parse_checklist_payload(_minimal({}))
    assert draft.items[0].diagnosis_mode == "file"

    draft = parse_checklist_payload(_minimal({"diagnosis_mode": "weird"}))
    assert draft.items[0].diagnosis_mode == "file"
```

（若 `_minimal` 与现有 fixture 重复，优先复用测试内 helper。）

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_checklist_agent_os.py::test_parse_checklist_payload_diagnosis_mode_defaults_and_normalizes -v`

Expected: FAIL

- [ ] **Step 3: Implement normalize + parse + merge + persist + get_report**

在 `checklist_agent_os.py`：

```python
_DIAGNOSIS_MODE_VALUES = frozenset({"file", "offline"})

def _normalize_diagnosis_mode(value: Any) -> str:
    if isinstance(value, str) and value.strip() in _DIAGNOSIS_MODE_VALUES:
        return value.strip()
    return "file"
```

在构造每个 `ChecklistItemDraft` 时传入 `diagnosis_mode=_normalize_diagnosis_mode(row.get("diagnosis_mode"))`；category remap 复制字段时一并带上。

`checklist_merge.py`：重建 `ChecklistItemDraft(...)` 时透传 `diagnosis_mode=item.diagnosis_mode or "file"`。

`checklist_service.py`：

- `_CONTENT` 旁增加 `_DIAGNOSIS_MODE_VALUES = {"file", "offline"}`（校验可选：非法已在 parse 归一，落库再防御一次）
- `ChecklistItem(... diagnosis_mode=item.diagnosis_mode or "file")`
- `get_report` item dict 增加 `"diagnosis_mode": item.diagnosis_mode or "file"`

`checklist_context.py` 的 `SYSTEM_INSTRUCTIONS` 追加规则：

```text
9. 每条检查项必须输出 diagnosis_mode：file 或 offline。
   打印、装订、密封、签字盖章等无法靠投标文件电子正文核验的要求标 offline；其余标 file。
```

（原第 8/输出句序号顺延。）

`fake_checklist_invoke.py`：生成 item 时默认 `"diagnosis_mode": "file"`；若标题/句子含「装订」「打印」「密封」「盖章」可标 `offline`（仅测试假数据便利，**不是**生产启发式）。

- [ ] **Step 4: Run checklist tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_checklist_agent_os.py \
  tests/test_checklist_merge.py \
  tests/test_checklist_service.py \
  tests/test_checklist_api.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/checklist_agent_os.py backend/app/engine/checklist_merge.py \
  backend/app/services/checklist_service.py backend/app/services/checklist_context.py \
  backend/tests/fake_checklist_invoke.py backend/tests/test_checklist_agent_os.py \
  backend/tests/test_checklist_service.py backend/tests/test_checklist_api.py \
  backend/tests/test_checklist_merge.py
git commit -m "$(cat <<'EOF'
feat: parse and persist checklist diagnosis_mode

EOF
)"
```

---

### Task 6: Scheduler offline 分流直写 `manual_required`

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/app/services/report.py`
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/tests/test_report.py`
- Modify: `backend/tests/test_batch_diagnosis.py`（若需）

常量（建议放 `scheduler.py` 顶部或小型 `offline_diagnosis.py`；**优先**放 scheduler 旁私有函数，避免新模块过度设计）：

```python
OFFLINE_EVIDENCE = "未检索文件（线下核验项）"
OFFLINE_SUGGESTION = (
    "该项属于打印/装订/密封等线下要求，需人工核验纸质或现场材料，系统不进行文件诊断"
)
```

- [ ] **Step 1: Add pure helpers and failing unit tests**

先在 `scheduler.py` 增加可单测函数：

```python
def _split_items_by_diagnosis_mode(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    offline: list[dict] = []
    file_items: list[dict] = []
    for item in items:
        mode = item.get("diagnosis_mode") or "file"
        if mode == "offline":
            offline.append(item)
        else:
            file_items.append(item)
    return offline, file_items


def _offline_batch_result(item: dict) -> "BatchItemResult":
    from app.engine.base import BatchItemResult
    import json

    tags: list[str] = []
    rules = item.get("consequence_rules") or {}
    if isinstance(rules, dict):
        tags = [k for k in rules if isinstance(k, str)]
    description = str(item.get("requirement") or item.get("title") or "")
    return BatchItemResult(
        checklist_item_id=item["id"],
        compliance="manual_required",
        consequence_tags=tags,
        evidence=OFFLINE_EVIDENCE,
        suggestion=OFFLINE_SUGGESTION,
        description=description,
    )
```

单元测试：

```python
def test_split_and_offline_result():
    from app.services.scheduler import (
        _split_items_by_diagnosis_mode,
        _offline_batch_result,
    )

    offline, file_items = _split_items_by_diagnosis_mode(
        [
            {"id": "a", "diagnosis_mode": "offline", "title": "装订", "requirement": "胶装"},
            {"id": "b", "diagnosis_mode": "file", "title": "执照"},
            {"id": "c", "title": "缺省"},
        ]
    )
    assert [i["id"] for i in offline] == ["a"]
    assert [i["id"] for i in file_items] == ["b", "c"]
    result = _offline_batch_result(offline[0])
    assert result.compliance == "manual_required"
    assert result.evidence == "未检索文件（线下核验项）"
```

- [ ] **Step 2: Run unit test — expect fail then implement helpers + phase loop**

改写 `_run_diagnosis_phase` 内 category 循环为：

```python
offline_items, file_items = _split_items_by_diagnosis_mode(category_items)

# Build results aligned to original category_items order
result_by_id: dict[str, BatchItemResult] = {}
for item in offline_items:
    result_by_id[item["id"]] = _offline_batch_result(item)

if file_items:
    retrieved_chunks = await retrieval.retrieve_for_category(
        task_id=task_id,
        category=category,
        items=file_items,
    )
    batch_results = await engine.diagnose_category(
        task_id=task_id,
        category=category,
        items=file_items,
        retrieved_chunks=retrieved_chunks,
    )
    assert_batch_complete(file_items, batch_results)
    for batch_result in batch_results:
        result_by_id[batch_result.checklist_item_id] = batch_result

ordered_pairs = [
    (item, result_by_id[item["id"]]) for item in category_items
]
# then persist DiagnosisResult rows from ordered_pairs (same fields as today)
```

全 offline category：不调用 `retrieve_for_category` / `diagnose_category`。

- [ ] **Step 3: Thin async test for `_run_diagnosis_phase` with spies**

不跑完整 upload 流水线：手动插入 `DiagnosisTask`，monkeypatch `get_report` / retrieval / engine，然后调用 `_run_diagnosis_phase`，用 session 查 `DiagnosisResult`。

```python
@pytest.mark.asyncio
async def test_run_diagnosis_phase_mixed_modes(monkeypatch, client):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app import db
    from app.engine.base import BatchItemResult
    from app.models import DiagnosisResult, DiagnosisTask
    from app.services import scheduler

    calls = {"retrieve": 0, "diagnose": 0}

    class R:
        async def retrieve_for_category(self, **kwargs):
            calls["retrieve"] += 1
            assert [i["id"] for i in kwargs["items"]] == ["file-1"]
            return []

    class E:
        def __init__(self, *a, **k):
            pass

        async def diagnose_category(self, **kwargs):
            calls["diagnose"] += 1
            item = kwargs["items"][0]
            return [
                BatchItemResult(
                    checklist_item_id=item["id"],
                    compliance="satisfied",
                    consequence_tags=[],
                    evidence="e",
                    suggestion="s",
                    description="d",
                )
            ]

    monkeypatch.setattr(scheduler, "build_retrieval_provider", lambda: R())
    monkeypatch.setattr(scheduler, "MockBatchDiagnosisEngine", E)

    async def fake_report(task_id):
        del task_id
        return {
            "categories": [
                {
                    "id": "c1",
                    "name": "c",
                    "items": [
                        {
                            "id": "offline-1",
                            "title": "密封",
                            "requirement": "密封",
                            "diagnosis_mode": "offline",
                            "consequence_rules": {},
                        },
                        {
                            "id": "file-1",
                            "title": "执照",
                            "requirement": "执照",
                            "diagnosis_mode": "file",
                            "consequence_rules": {},
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(scheduler, "get_report", fake_report)

    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="task-offline-mix",
                status="diagnosing",
                progress_done=0,
                progress_total=2,
                tender_path="t.pdf",
                bid_path="b.docx",
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    assert await scheduler._run_diagnosis_phase("task-offline-mix") is True
    assert calls["retrieve"] == 1
    assert calls["diagnose"] == 1

    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(DiagnosisResult).where(
                    DiagnosisResult.task_id == "task-offline-mix"
                )
            )
        ).scalars().all()
    statuses = {r.checklist_item_id: r.compliance_status for r in rows}
    assert statuses["offline-1"] == "manual_required"
    assert statuses["file-1"] == "satisfied"
```

（`DiagnosisTask` 必填字段以实现时 `models.py` 为准补全。）

- [ ] **Step 4: Report label**

`report.py`：

```python
COMPLIANCE_LABELS = {
    ...
    "manual_required": "需线下核验",
}
```

`test_report.py` 增加含 `manual_required` 的结果，断言 Markdown 出现「需线下核验」。

- [ ] **Step 5: Run tests and commit**

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_scheduler.py \
  tests/test_report.py \
  tests/test_batch_diagnosis.py -v
```

```bash
git add backend/app/services/scheduler.py backend/app/services/report.py \
  backend/tests/test_scheduler.py backend/tests/test_report.py
git commit -m "$(cat <<'EOF'
feat: skip file diagnosis for offline checklist items

EOF
)"
```

---

## Phase C — 前端与 Agent 配置

### Task 7: 前端标签

**Files:**
- Modify: `frontend/src/components/ResultTable.jsx`
- Modify: `frontend/src/components/ChecklistReport.jsx`

- [ ] **Step 1: ResultTable**

```javascript
const COMPLIANCE_LABELS = {
  satisfied: '满足',
  violated: '违反',
  cannot_satisfy: '不能满足',
  insufficient_evidence: '证据不足',
  manual_required: '需线下核验',
  // ...existing chinese aliases
}
```

- [ ] **Step 2: ChecklistReport**

在标题列或独立列旁：当 `item.diagnosis_mode === 'offline'` 时渲染 `<span className="checklist-offline-tag">线下核验</span>`（复用现有次要文字样式，避免新设计体系）。确保 API 返回的 item 已含 `diagnosis_mode`（Task 5 `get_report` / checklist API）。

- [ ] **Step 3: Manual smoke**

Run frontend dev server if needed; otherwise visual check optional. No Jest required unless repo already has component tests.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ResultTable.jsx frontend/src/components/ChecklistReport.jsx
git commit -m "$(cat <<'EOF'
feat: show offline diagnosis labels in task UI

EOF
)"
```

---

### Task 8: 更新并发布检查项 Agent 配置

**Files:**
- Modify: `docs/agents_config/tender_checklist_generator.json`
- Use skill: `.cursor/skills/agent-create-publish/SKILL.md`（按该 skill 发布）

- [ ] **Step 1: Schema + prompt**

在 `outputSchema` → `items.children` 增加：

```json
{
  "id": "f_item_diagnosis_mode",
  "name": "diagnosis_mode",
  "description": "file=需对投标文件诊断；offline=打印装订密封等线下核验，不查文件",
  "type": "string",
  "required": true,
  "children": null,
  "itemType": null
}
```

在 `systemPrompt` 固定规则中增加与 `SYSTEM_INSTRUCTIONS` 一致的 `diagnosis_mode` 说明。

- [ ] **Step 2: Publish via agent-create-publish skill**

按 `.cursor/skills/agent-create-publish/SKILL.md` 更新并发布 `tender_checklist_generator` / `tender_checklist_generator_app`。发布后回写 JSON 中的 version/publishedAt（若 skill 要求）。

- [ ] **Step 3: Commit config**

```bash
git add docs/agents_config/tender_checklist_generator.json
git commit -m "$(cat <<'EOF'
chore: add diagnosis_mode to checklist generator agent config

EOF
)"
```

---

### Task 9: 全量回归与验收对照

- [ ] **Step 1: Run backend suite**

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

Expected: 全绿

- [ ] **Step 2: Spec acceptance checklist**

对照 `2026-07-19-offline-diagnosis-and-real-interpretation-design.md` 验收标准 1–7：

1. 解读路径为 Agent OS（测试为 stub；生产为真实 invoke）— 代码层无 `MockInterpretationAgent()` 生产调用  
2. `diagnosis_mode` 可落库；漏标 → `file`  
3. offline → `manual_required`，无检索/批诊断调用  
4. file 项仍走检索 + Mock 批诊断  
5. 解读失败不进入检查项（有测试）  
6. 主链路 Mock 仅剩批诊断结论（明确排除项）  
7. 默认测试不打真实 Agent OS  

- [ ] **Step 3: Final commit only if docs/comments need sync**（无则跳过）

---

## Spec Coverage (self-review)

| Spec 要求 | Task |
|---|---|
| 真实解读、等解析、不回退 Mock | 1–3 |
| `diagnosis_mode` file/offline，默认 file | 4–5 |
| offline 跳过检索/引擎，`manual_required` 固定文案 | 6 |
| 报告/前端「需线下核验」 | 6–7 |
| 检查项「线下核验」标签 | 7 |
| Agent 配置更新发布 | 8 |
| 不做真实诊断 / 不做关键词生产启发式 / 不抽 Router | 范围外（未建任务） |

## Placeholder / consistency notes

- 合规状态统一为 `manual_required`（不是 `offline_check`）。
- `AgentOSClient` 使用 `settings=` 关键字参数。
- offline evidence/suggestion 字符串与 spec 完全一致。
- 解读移植以分支 `feat/agent-os-tender-interpretation` 为准，禁止重建已存在的 `AgentOSClient`。
