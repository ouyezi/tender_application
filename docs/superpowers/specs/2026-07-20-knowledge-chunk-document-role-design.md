# 知识块 document_role 设计

## 背景与目标

诊断与检索需要明确知识块来自**招标文件**、**标书**还是**其它**附件。当前仅通过 `KnowledgeChunk.file_id` 间接关联 `DiagnosisTask.tender_file_id` / `bid_file_id`，块上无显式角色；`KnowledgeChunk.source` 表示正文提取方式（`native_text|ocr|table`），易混淆。

**目标：** 为知识块增加 `document_role`（`tender|bid|other`），新索引写入；旧块读时动态推断；检索与批诊断证据透传角色。

## 已确认决策

| 决策点 | 选择 |
|---|---|
| 字段名 | `document_role`（不与 `source` / 检查项 `file_role` 混名） |
| 取值 | `tender` \| `bid` \| `other` |
| 与检查项对齐 | 语义与 `content_target.file_role` 一致 |
| 历史数据 | **不批量回填**；NULL 时读路径按 `file_id` 推断 |
| 写入 | 新索引在 `write_segments` 时显式写入 |

## 非目标

- 修改 `KnowledgeChunk.source` 含义
- 强制用户重新解析/全量重建索引
- 检查项 schema 重命名（仍用 `file_role`）
- 工作区 UI 大改（API 先透传，前端可后续消费）

---

## 第一节：数据模型与写入（已确认）

### 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `document_role` | `String(16)` nullable | `tender` \| `bid` \| `other`；新索引必填 |

### 写入

`index_scheduler._run_job` 在 enrich 后、`write_segments` 前：

1. 加载 `DiagnosisTask`
2. `document_role = resolve_document_role(file_id=..., tender_file_id=..., bid_file_id=..., stored_role=None)`
3. `write_segments(..., document_role=document_role)`

### 判定规则

1. `file_id == task.tender_file_id` → `tender`
2. `file_id == task.bid_file_id` → `bid`
3. 其余 → `other`
4. `task` 缺失 → `other`

### 迁移

- `models.KnowledgeChunk` 增加 nullable 列
- 依赖 `_migrate_sqlite_columns` 自动 ADD COLUMN
- **不做** UPDATE 回填

---

## 第二节：读路径推断与透传

### 集中 helper

新增 `backend/app/services/retrieval/document_role.py`：

```python
VALID_DOCUMENT_ROLES = frozenset({"tender", "bid", "other"})

def resolve_document_role(
    *,
    file_id: str,
    tender_file_id: str | None,
    bid_file_id: str | None,
    stored_role: str | None = None,
) -> str:
    if stored_role in VALID_DOCUMENT_ROLES:
        return stored_role
    if tender_file_id and file_id == tender_file_id:
        return "tender"
    if bid_file_id and file_id == bid_file_id:
        return "bid"
    return "other"
```

所有读路径**必须**经此函数，禁止散落 duplicate 逻辑。

### 展示用 `file_label`（不落库）

读 API 时通过 `WorkspaceFile.label`（如「招标文件」「标书」）附加 `file_label`，便于 UI/debug；不新增 DB 列。

### 透传面

| 位置 | 变更 |
|------|------|
| `retrieval/persist.write_segments` | 写入 `document_role` |
| `retrieval/browse._chunk_to_list_item` / `_chunk_to_detail` | 增加 `document_role`、`file_label` |
| `engine/base.RetrievalHit` | 增加 `document_role: str = ""` |
| `retrieval/provider._chunk_to_hit` | 传入 task 上下文或预解析 role，填充 `document_role` |
| `engine/base.RetrievedChunk` | 增加 `document_role: str = ""` |
| `engine/retrieval_workspace.retrieve_for_category` | 从 `RetrievalHit` 拷贝至 `RetrievedChunk` |
| `engine/batch_diagnosis_agent_os` | `retrieved_chunks` JSON 每项增加 `document_role` |
| `retrieval/debug_types.hit_dict` | 增加 `document_role` |

### provider 推断方式

`retrieve()` 已加载 `DiagnosisTask` 或通过 `_resolve_file_role` 拿 task 时，将 `tender_file_id` / `bid_file_id` 传入 `_chunk_to_hit`：

```python
document_role=resolve_document_role(
    file_id=chunk.file_id,
    tender_file_id=task.tender_file_id,
    bid_file_id=task.bid_file_id,
    stored_role=chunk.document_role,
)
```

### 批诊断 Agent 载荷

`retrieved_chunks` 每项由：

```json
{"chunk_id": "...", "text": "...", "location": "..."}
```

扩展为：

```json
{"chunk_id": "...", "text": "...", "location": "...", "document_role": "bid"}
```

**不修改** `tender_batch_diagnosis` Agent input schema（仍为 JSON 字符串）；额外键在 JSON 数组元素内，向后兼容。

可选后续：在 `batch_diagnosis_context.SYSTEM_INSTRUCTIONS` 补充「证据须注意 document_role」——本期可不强制改 Agent 提示词。

### browse 列表 `file_label` 批量加载

`list_chunks` 对页内 distinct `file_id` 一次查询 `WorkspaceFile`，构建 `file_id → label` map，避免 N+1。

---

## 错误处理

| 场景 | 行为 |
|------|------|
| 旧块 `document_role` NULL | 读时推断，不报错 |
| `file_id` 找不到 WorkspaceFile | `file_label` 回退为 `file_id` 或空串 |
| 非法 stored_role（脏数据） | 忽略 stored，走 file_id 推断 |

---

## 测试计划

| # | 用例 | 断言 |
|---|------|------|
| 1 | `resolve_document_role` 单元测试 | stored 优先；NULL 时 tender/bid/other |
| 2 | `write_segments` 集成 | 新块 `document_role` 正确落库 |
| 3 | `test_index_scheduler` 回归 | 索引后 chunk 含 role |
| 4 | browse API | 返回 `document_role` + `file_label` |
| 5 | `retrieve_for_category` | `RetrievedChunk.document_role` 非空 |
| 6 | batch diagnosis payload | invoke JSON 含 `document_role` |

---

## 文件变更清单

| 文件 | 操作 |
|------|------|
| `backend/app/models.py` | `KnowledgeChunk.document_role` |
| `backend/app/services/retrieval/document_role.py` | **新建** helper |
| `backend/app/services/retrieval/persist.py` | 写入 role |
| `backend/app/services/index_scheduler.py` | 解析 role 传入 persist |
| `backend/app/services/retrieval/browse.py` | API 透传 |
| `backend/app/services/retrieval/provider.py` | `_chunk_to_hit` 填充 |
| `backend/app/engine/base.py` | `RetrievalHit` / `RetrievedChunk` 字段 |
| `backend/app/engine/retrieval_workspace.py` | 拷贝 role |
| `backend/app/engine/batch_diagnosis_agent_os.py` | payload 字段 |
| `backend/app/services/retrieval/debug_types.py` | debug hit 字段 |
| `backend/tests/test_document_role.py` | **新建** |
| 相关 index/retrieval/batch 测试 | 更新断言 |

---

## 成功标准

1. 新索引知识块 DB 中有正确 `document_role`。
2. 旧块 browse/检索/批诊断仍能工作，读结果含推断后的 role。
3. 诊断证据 JSON 可区分 `tender` / `bid` / `other`。
4. 现有测试绿 + 新增用例通过。
