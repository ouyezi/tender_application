# 知识块富化分批策略设计

## 背景与问题

标书索引阶段调用 `retrieval_chunk_enricher_app` 时，`IndexJob` 失败并写入：

```
bid_index_failed:Agent OS invoke HTTP 500 for app retrieval_chunk_enricher_app
```

底层模型 API 返回：

```
400 InternalError.Algo.InvalidParameter: Range of input length should be [1, 991808]
```

**根因：** `AgentOSChunkEnricher.enrich_many()` 将整份文件全部 fine + large 段落的完整 `text`，连同 `catalog_json` 与 prompt 模板，**单次 invoke** 提交。大标书段落数量多、large 段含整章正文，总 input 超出模型上限。

**设计文档原意：** `segments_json` 字段描述为「本批段落」，但实现从未分批。

## 已确认决策

| 决策点 | 选择 |
|---|---|
| 分批策略 | **C + D**：字符预算 + 段数上限；fine / large 分层 |
| segments 字符预算 | **每批 < 10k**（不含 catalog / prompt 模板） |
| 段数上限 | **每批 ≤ 5 段** |
| large 段 | **逐段单批**；text 超限时截断 |
| large 截断上限 | **8000 字符**（前缀截断，chunk_id 不变） |
| 实现位置 | **方案 1**：`AgentOSChunkEnricher.enrich_many` 内部分批 |
| IndexJob 进度 | 本期不做（YAGNI） |
| 10k 口径 | segments 序列化 JSON 的有效内容预算 |

## 目标

1. 单次 invoke 的 segments 有效字符 < 10k。
2. fine 段按混合约束动态切批；large 段逐段 invoke。
3. 保持 chunk_id 1:1，不增删段。
4. 对外 `ChunkEnricher` 协议与 `index_scheduler` 调用方式不变。
5. 失败错误信息含批次上下文，便于排查。

## 非目标

- 修改 Agent OS `retrieval_chunk_enricher` 提示词（可选后续增强截断说明）
- `IndexJob` 分批进度回写
- Wiki / QueryRewriter / AiReranker 分批（本期仅 ChunkEnricher）
- 后端对 HTTP 500 的 special-case 容错

## 架构

```
index_scheduler._run_job
  └─ get_chunk_enricher().enrich_many(all segments)   # 调用方式不变
       └─ AgentOSChunkEnricher.enrich_many            # 内部分批
            ├─ split fine → batches (≤5 seg, <10k chars)
            ├─ large → one segment per batch (text truncate 8k)
            ├─ for each batch: invoke_app(segments_json subset)
            └─ merge + validate all chunk_ids present
```

## 分批算法

### 字符计量

对一批 segments，构造 invoke payload 中的 segment dict 列表：

```python
{
    "chunk_id": seg.chunk_id,
    "title_path": seg.title_path,
    "text": seg.text,           # large 可能已截断
    "segment_level": seg.segment_level,
}
```

**批次字符数** = `len(json.dumps(batch_dicts, ensure_ascii=False))`

### fine 段切批（贪心）

1. 按文档顺序遍历 fine segments。
2. 尝试加入当前批；若加入后超过 `ENRICH_BATCH_MAX_CHARS` 或段数 > `ENRICH_BATCH_MAX_SEGMENTS`，则开启新批。
3. 单段 serialized 长度 ≥ 10k 时：该段**独占一批**（不再拆 chunk_id）；若仍 ≥ 10k，对该段 text 截断至使 serialized < 10k（保留最小必要 metadata）。

### large 段（分层 D）

1. 每 large 段单独一批（1 段 / invoke）。
2. 若 `len(text) > ENRICH_LARGE_MAX_TEXT_CHARS`（8000），截断为 `text[:8000]` 再序列化。
3. chunk_id、title_path、segment_level 不变；模型基于可见前缀生成 title/summary/description。

### 合并与校验

- 各批 invoke 返回的 rows 按 `chunk_id` 合并。
- 最终 `by_id` 必须覆盖入参全部 `chunk_id`；否则 `ChunkEnrichResponseError`。
- 标签过滤逻辑保持现有 `_filter_tags` 行为。

## 配置项

新增 `backend/app/config.py` 常量：

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `ENRICH_BATCH_MAX_CHARS` | `10000` | fine 批 segments JSON 字符上限 |
| `ENRICH_BATCH_MAX_SEGMENTS` | `5` | fine 批最多段数 |
| `ENRICH_LARGE_MAX_TEXT_CHARS` | `8000` | large 段送入模型的 text 前缀上限 |

可选：`config.local.json.example` 增加 `chunkEnricher` 段供将来 env 覆盖；**本期仅代码常量**，与现有 `INDEX_TAG_MIN_CONFIDENCE` 风格一致。

## 代码改动

### 修改

| 文件 | 改动 |
|------|------|
| `backend/app/services/retrieval/enricher_agent_os.py` | 分批 helpers；`enrich_many` 循环 invoke；large 截断；错误上下文 |
| `backend/app/config.py` | 三个 `ENRICH_*` 常量 |
| `backend/tests/test_retrieval_enricher_agent_os.py` | 新增分批/截断/多 invoke 测试 |

### 不修改

| 文件 | 原因 |
|------|------|
| `backend/app/services/index_scheduler.py` | 协议不变 |
| `docs/agents_config/retrieval_chunk_enricher.json` | 输入 schema 不变 |
| `backend/app/services/agent_os.py` | 复用现有 client |

### 新增 helpers（`enricher_agent_os.py`）

```python
def _segment_dict(seg: SegmentDraft, *, text_override: str | None = None) -> dict: ...

def _batch_char_size(batch: list[dict]) -> int: ...

def _truncate_text_for_budget(text: str, max_chars: int, overhead: int) -> str: ...

def _split_fine_batches(
    segments: list[SegmentDraft],
    *,
    max_chars: int,
    max_segments: int,
) -> list[list[SegmentDraft]]: ...

def _prepare_large_batches(
    segments: list[SegmentDraft],
    *,
    max_text_chars: int,
) -> list[list[SegmentDraft]]: ...
```

### 错误信息增强

invoke 失败时，在现有 `AgentOSError` 之上或由 `enrich_many` 包装：

```
ChunkEnrichResponseError: enrich batch 3/12 failed (fine, 4 segments, ~9800 chars): ...
```

## 测试计划

| # | 用例 | 断言 |
|---|------|------|
| 1 | 现有 3 个 enricher 测试 | 仍 PASS（单批行为不变） |
| 2 | `test_enrich_splits_fine_into_multiple_invokes` | 6 个 fine 各 3k 字 → invoke ≥ 2 次；chunk_id 全覆盖 |
| 3 | `test_enrich_respects_max_segments_per_batch` | 10 个短 fine → 每批 ≤ 5 段 |
| 4 | `test_enrich_large_one_per_batch_with_truncation` | 1 large 20k 字 → 1 invoke；payload text len ≤ 8000 |
| 5 | `test_enrich_mixed_fine_and_large` | fine 分批 + large 逐段；总 chunk_id 一致 |

测试通过注入 `invoke_app` 计数调用次数与 payload 大小，不依赖真实 Agent OS。

## 错误处理

| 场景 | 行为 |
|------|------|
| 某批 Agent OS 500/超时 | 整次 enrich_many 失败；IndexJob.error_message 含批次信息 |
| 某批返回缺 chunk_id | `ChunkEnrichResponseError` |
| 空 segments 列表 | 直接返回 `[]`，不 invoke |

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 10k 过小导致 invoke 次数很多 | 可后续调高常量；默认先保安全 |
| large 截断影响 summary 质量 | 仅 large 段；fine 保持全文（在 10k 批内） |
| 单 fine 段 > 10k | 独占一批并截断 text 至预算内 |

## 成功标准

1. 大标书索引不再因 input length 400 失败。
2. 单元测试覆盖分批与 large 截断。
3. 现有 enricher / index_scheduler 测试仍绿。
