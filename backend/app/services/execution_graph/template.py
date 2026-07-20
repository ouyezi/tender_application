TASK_GRAPH_NODES: list[dict] = [
    {"node_key": "start", "label": "开始", "kind": "terminal", "sort_order": 0},
    {"node_key": "parse.tender", "label": "招标文件解析", "kind": "file", "sort_order": 10},
    {"node_key": "parse.bid", "label": "标书解析", "kind": "file", "sort_order": 20},
    {"node_key": "index.segments", "label": "索引分段", "kind": "stage", "sort_order": 30},
    {"node_key": "index.enrich", "label": "块增强", "kind": "stage", "sort_order": 40},
    {"node_key": "index.fts", "label": "全文索引", "kind": "stage", "sort_order": 50},
    {"node_key": "index.vectors", "label": "向量索引", "kind": "stage", "sort_order": 60},
    {"node_key": "index.wiki", "label": "Wiki 构建", "kind": "stage", "sort_order": 70},
    {"node_key": "index.gate", "label": "等待标书索引就绪", "kind": "gate", "sort_order": 80},
    {"node_key": "interpret", "label": "招标文件解读", "kind": "stage", "sort_order": 90},
    {"node_key": "checklist.generate", "label": "检查项生成", "kind": "stage", "sort_order": 100},
    {"node_key": "diagnosis", "label": "标书诊断", "kind": "container", "sort_order": 110},
    {"node_key": "report.generate", "label": "报告生成", "kind": "stage", "sort_order": 120},
    {"node_key": "end", "label": "完成", "kind": "terminal", "sort_order": 130},
]

# Two parallel tracks after start:
#   tender: parse.tender → interpret → checklist.generate
#   bid:    parse.bid → index.* → index.wiki → index.gate
# Diagnosis category nodes (added at runtime) fan in from checklist + index.gate
# and fan out in parallel to report.generate.
TASK_GRAPH_EDGES: list[dict] = [
    {"from_key": "start", "to_key": "parse.tender", "edge_kind": "sequential"},
    {"from_key": "start", "to_key": "parse.bid", "edge_kind": "parallel"},
    {"from_key": "parse.tender", "to_key": "interpret", "edge_kind": "depends_on"},
    {"from_key": "interpret", "to_key": "checklist.generate", "edge_kind": "sequential"},
    {"from_key": "parse.bid", "to_key": "index.segments", "edge_kind": "sequential"},
    {"from_key": "index.segments", "to_key": "index.enrich", "edge_kind": "sequential"},
    {"from_key": "index.enrich", "to_key": "index.fts", "edge_kind": "sequential"},
    {"from_key": "index.fts", "to_key": "index.vectors", "edge_kind": "sequential"},
    {"from_key": "index.vectors", "to_key": "index.wiki", "edge_kind": "sequential"},
    {"from_key": "index.wiki", "to_key": "index.gate", "edge_kind": "sequential"},
    {"from_key": "report.generate", "to_key": "end", "edge_kind": "sequential"},
]
