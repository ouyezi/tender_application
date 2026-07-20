# 检索父块上下文解析（Context Resolver）设计规格

**日期：** 2026-07-20  
**状态：** 设计已确认，待用户复核正文  
**范围：** `precise_search` 与 `collection` 检索后处理——智能判断是否补充父块/兄弟块上下文，并在返回父块时剔除已在结果集中的子块正文，避免重复与误判  
**前置：**

- `2026-07-17-workspace-document-retrieval-design.md`（fine/large 双粒度、`_expand_fine_to_large`）
- `2026-07-18-agent-os-retrieval-agents-design.md`（查询重写、AI 重排 Agent OS 接入形态）

---

## 1. 目标与范围

### 1.1 问题

当前 `precise_search` / `collection` 在 fine 块命中后，通过 `_expand_fine_to_large` **无条件**上卷为完整 large 子树（`start_offset → subtree_end`）。这导致两类问题：

1. **上下文缺失**：只命中子块（如「子公司资质」），漏掉同父章节下的兄弟块（如「主公司对子公司授权书」），诊断引擎误判「资质有问题」。
2. **内容重复**：large 子树包含所有子章节正文；多个子块同时命中或父块与子块均返回时，下游收到大量重叠文本。

用户需要：根据**内容、目录结构（title_path）、概述（summary）**，判断是否返回父块上下文；返回父块时**排除**已在结果集中的子块 span，避免重复。

### 1.2 已确认决策

| 决策点 | 选择 |
|--------|------|
| 返回形态 | **方案 B**：多条独立 `RetrievalHit`，由诊断引擎自行组合 |
| 父块过大策略 | 父块可返回正文 **> 10000 字符** 时不返回整段父块，改为命中子块 ±N 个文档顺序上的兄弟 fine 块 |
| 决策机制 | **方案 C（混合）**：结构规则预筛候选 → Agent OS AI 最终裁决 |
| 阈值 | 默认 10000，配置项 `RETRIEVAL_PARENT_MAX_CHARS`；兄弟窗口默认 ±2，配置项 `RETRIEVAL_SIBLING_WINDOW` |
| 实现方案 | **方案 3**：索引存 `intro_end` 元数据 + 查询期 `ContextResolver` |

### 1.3 成功标准

1. 命中「子公司资质」fine 块、query 涉及「独立法人资格/授权」时，Resolver 补充「主公司授权」兄弟块和/或父块引言。
2. 同一父章节多子块命中时，若返回 `parent_body`，其 `text` 已剔除结果集中已有 fine/sibling hit 的 span。
3. 父块可返回正文 ≤ 10000 字符时返回 `parent_intro` 或 `parent_body`；超过则只返回窗口内相关兄弟块。
4. 原始 fine 命中默认保留（`context_role=matched`）。
5. Agent 失败时可规则降级，标记 `degraded=true`，不阻断检索。

### 1.4 本期范围

- 新增 `ContextResolver` 模块（规则 + Agent OS）
- `KnowledgeChunk` large 段增加 `intro_end` 字段
- `RetrievalHit` 增加 `context_role`、`derived_from`、`anchor_chunk_id`
- 替换 `precise_search` / `collection` 中的 `_expand_fine_to_large` 调用
- 知识调试台透传新字段

### 1.5 本期不做

- 不改向量 / FTS / Wiki 召回逻辑
- 不改为诊断引擎返回单一「上下文包」
- `large_segments` / `full_document` 保持原样
- 不新增 `parent_intro` 独立 segment 类型（仅用偏移切分）

---

## 2. 架构与数据流

### 2.1 流程变更

```text
召回 + 合并打分
  → AI Rerank（现有，基于 title/summary）
  → ContextResolver
      ① 规则预筛：为每个 fine 命中生成候选动作
      ② AI 裁决：Agent OS 选定 actions + sibling_chunk_ids
      ③ 文本物化：切 parent_intro / 剔除子块 span / 选兄弟块
      ④ 去重合并：chunk_id + context_role 去重
  → 返回多条 RetrievalHit
```

### 2.2 索引层改动

在 `KnowledgeChunk`（large 段）新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `intro_end` | `Integer` nullable | 父章节自身正文结束偏移 = 第一个子节点 `start_offset`；无子节点或无独立引言时为 null |

在 `materialize_segments()` 建 large 段时：取 `children[0].start_offset` 作为 `intro_end`；若 `intro_end <= start` 则置 null。

**历史数据**：读路径 `intro_end` 为 null 时，可从 `tree.json` 动态计算（与索引写入逻辑一致）；新索引必须写入。

### 2.3 RetrievalHit 扩展

| 字段 | 类型 | 说明 |
|------|------|------|
| `context_role` | str | `matched` \| `parent_intro` \| `parent_body` \| `sibling` |
| `derived_from` | str \| null | 衍生自哪个 large `chunk_id` |
| `anchor_chunk_id` | str \| null | 触发 Resolver 的原始 fine `chunk_id` |

`text` 始终是**已切分后的展示正文**；下游无需再做 exclusion。

### 2.4 文本物化规则

设 `parent_chars = intro_end - start`（有 intro 时）或 `large.end - large.start`（无 intro、需 parent_body 时）。

**情形 A — 有 intro 且 `parent_chars ≤ RETRIEVAL_PARENT_MAX_CHARS`：**

- 追加 hit：`context_role=parent_intro`，`text = markdown[start:intro_end]`

**情形 B — 需 parent_body 且剔除后长度 ≤ RETRIEVAL_PARENT_MAX_CHARS：**

- `text = large 全文 − 结果集中同父 fine/sibling hits 的 [start,end) span`（按 start 排序合并区间后切除）
- `context_role=parent_body`

**情形 C — 父块过大（intro 或 parent_body 剔除后仍 > 阈值）：**

- **不返回**整段父块
- 在命中 fine 的**同父 direct children** 中，按文档顺序取 `[hit_index − N, hit_index + N]` 窗口（`RETRIEVAL_SIBLING_WINDOW`，默认 N=2）
- AI 从窗口内筛选与 query 相关的 `sibling_chunk_ids`；AI 未返回时 fallback 为窗口内全部 fine 兄弟（不含 anchor 自身若已在 matched 中）

**情形 D — 无 large 祖先 / intro 为空且父块过大：**

- 仅保留 `context_role=matched` 的 fine hit

**原始 fine 命中**：默认始终输出；除非未来扩展「完全冗余」判定（本期不做自动剔除 matched）。

---

## 3. ContextResolver：规则 + AI 裁决

### 3.1 模块职责

新文件：`backend/app/services/retrieval/context_resolver.py`

| 函数 | 职责 |
|------|------|
| `resolve_context(...)` | 入口：rerank 后的 hits + query + session → 扩展后的 hits |
| `_rule_candidates(...)` | 规则预筛 |
| `_materialize_parent_intro(...)` | 切 intro 文本 |
| `_materialize_parent_body(...)` | 切 parent_body 并剔除 span |
| `_select_siblings(...)` | 窗口 + AI 筛选兄弟块 |

Agent OS：`backend/app/services/retrieval/context_resolver_agent_os.py`  
应用名：`retrieval_context_resolver_app`

### 3.2 规则预筛

对每个 rerank 后 **fine 命中**，找**最近 large 祖先**（与现 `_expand_fine_to_large` 相同遍历 `ancestor_node_ids`）：

| 规则 | 候选动作 | 条件 |
|------|----------|------|
| R1 | `add_parent_intro` | large 存在且 `intro_end > start` |
| R2 | `add_parent_body` | 同 `parent_node_id` 下 rerank 结果中 fine 命中数 ≥ 2 |
| R3 | `add_siblings` | 父 large 可返回正文（intro 或 body）长度 > `RETRIEVAL_PARENT_MAX_CHARS` |
| R4 | `keep_only` | 无 large 祖先，或 intro 为空且 R3 成立 |

**关键词重叠（R2 增强）**：query / rewrite keywords 与父块 `title_path` + `summary` 做 jieba token 交集，≥1 词命中则追加 `add_parent_intro` 或 `add_siblings`（视大小）。

**同父聚合**：多个 fine 共享同一 `parent_node_id` 时，合并为一次 Resolver 调用（避免重复 Agent 请求），`anchor_chunk_ids` 列表传入。

### 3.3 AI 裁决

**输入（JSON）：**

```json
{
  "requirement": "检查项要求或 query",
  "query": "检索 query",
  "hits": [
    {
      "chunk_id": "chk_subsidiary",
      "title": "子公司资质",
      "summary": "...",
      "title_path": ["资格证明", "子公司资质"]
    }
  ],
  "parent": {
    "chunk_id": "lg_qual",
    "title": "资格证明",
    "summary": "...",
    "title_path": ["资格证明"],
    "intro_chars": 800,
    "total_chars": 45000
  },
  "siblings": [
    {
      "chunk_id": "chk_auth",
      "title": "主公司授权书",
      "summary": "...",
      "title_path": ["资格证明", "主公司授权书"],
      "distance": 1
    }
  ],
  "candidates": ["add_parent_intro", "add_siblings"]
}
```

**输出（JSON）：**

```json
{
  "actions": ["add_parent_intro", "add_siblings"],
  "sibling_chunk_ids": ["chk_auth"],
  "reason": "子公司资质需结合主公司授权书及章节引言中的法人资格要求"
}
```

**约束：**

- `actions` 必须是 `candidates` 的子集
- `sibling_chunk_ids` 必须是输入 `siblings` 中的 id
- 解析失败 → 规则 fallback（见 §4.2）

### 3.4 走查示例

**要求：** 响应人须为境内具有独立法人资格的企业或事业单位。

**命中：** fine「子公司营业执照/资质」  
**同父兄弟：** fine「主公司对子公司授权书」  
**父 large：** 「资格证明」章节，total_chars > 10000

```
规则：R1 + R3 → candidates = [add_parent_intro, add_siblings]
AI：actions = [add_parent_intro, add_siblings], sibling_chunk_ids = [chk_auth]

返回 3 条 hit：
  1. matched      → 子公司资质 fine（anchor）
  2. parent_intro → 章节引言（独立法人要求原文）
  3. sibling      → 主公司授权书 fine
```

诊断引擎组合三条证据，不再因「只看到子公司」误判。

---

## 4. 去重、错误处理与配置

### 4.1 去重键

| 键 | 说明 |
|----|------|
| `(chunk_id, context_role)` | 同一 chunk 不同 role 可共存（如 large 衍生 parent_intro 与 fine matched 不同 id） |
| `parent_intro` / `parent_body` | 使用合成 id：`{large_chunk_id}::intro` / `{large_chunk_id}::body`，避免与 large 原 id 冲突 |

同一 `anchor_chunk_id` 触发的 sibling 不重复追加。

### 4.2 降级

| 场景 | 行为 |
|------|------|
| Agent OS 超时 / 解析失败 | R1 有 intro → `add_parent_intro`；R3 → 窗口内全部 siblings；否则 `keep_only` |
| `intro_end` 缺失且 tree 不可用 | 跳过 parent_intro/body，仅 matched + 规则 siblings |
| 切分后 parent_body 为空 | 不追加 parent_body hit |

`RetrievalResult.degraded = true`（任一 anchor 走 fallback 时）。

### 4.3 配置项

```python
RETRIEVAL_PARENT_MAX_CHARS = 10_000   # 父块可返回正文上限
RETRIEVAL_SIBLING_WINDOW = 2          # 兄弟块文档顺序 ±N
RETRIEVAL_CONTEXT_RESOLVER_APP_NAME = "retrieval_context_resolver_app"
```

### 4.4 与旧规格的关系

`2026-07-17-workspace-document-retrieval-design.md` §4.3 规定「命中父章节时默认返回覆盖子树的 large 段」。本规格** supersede **该行为在 `precise_search` / `collection` 下的实现：改为 ContextResolver 驱动的多条 hit，不再无条件返回完整子树 large。

`large_segments` 仍按原规格返回完整 large 列表供智能体自行拆分。

---

## 5. 测试计划

### 5.1 单元测试

| 测试 | 断言 |
|------|------|
| `intro_end` 物化 | 有子节点的 large 段正确写入第一个子节点 offset |
| `_materialize_parent_intro` | 文本等于 `markdown[start:intro_end]` |
| `_materialize_parent_body` | 剔除 matched/sibling span 后无重叠 |
| `_select_siblings` | 窗口边界、distance 计算正确 |
| 规则 R1–R4 | 各候选条件独立触发 |
| 去重 | 同 `(chunk_id, context_role)` 不重复 |

### 5.2 集成测试（mock Agent OS）

| 场景 | 断言 |
|------|------|
| 子公司 + 授权书夹具 | 返回 matched + parent_intro + sibling |
| 同父多 fine 命中、小父块 | parent_body 剔除子 span |
| 大父块 >10000 | 无 parent_body，仅有 siblings |
| Agent 失败 | fallback + `degraded=true` |
| `collection` 模式 | 与 `precise_search` 共用 Resolver |

### 5.3 调试台

知识调试台 `precise_search` 响应展示 `context_role`、`derived_from`、`anchor_chunk_id`，便于人工验证 Resolver 决策。

---

## 6. 实现顺序建议

1. 模型 + `intro_end` 写入（`segments.py` / `persist.py` / migration）
2. `RetrievalHit` 字段扩展 + `_chunk_to_hit` 透传
3. `context_resolver.py` 规则与物化（无 Agent，纯规则测试）
4. `context_resolver_agent_os.py` + Agent 应用注册
5. 接入 `provider._precise_search` / `_collection`，移除 `_expand_fine_to_large` 直接调用
6. debug API / 前端抽屉展示新字段
7. 端到端夹具：子公司资质误判回归

---

## 7. 开放问题（实现阶段确认）

1. **Agent 应用 prompt**：需在 Agent OS 侧新建 `retrieval_context_resolver_app`，与 enricher/reranker 同级。
2. **reindex**：旧 chunk 无 `intro_end` 时读路径读 tree 兜底；是否提供「按需重建索引」管理入口本期 optional。
3. **large 直接命中**：rerank 结果含 large 段时，是否走 Resolver 或原样返回——建议 large 直接命中保持原样，仅 fine 触发 Resolver。
