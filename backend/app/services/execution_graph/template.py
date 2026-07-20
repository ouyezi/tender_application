TASK_GRAPH_NODES: list[dict] = [
    {"node_key": "start", "label": "开始", "kind": "terminal", "sort_order": 0},
    {"node_key": "parse.tender", "label": "招标文件解析", "kind": "file", "sort_order": 10},
    {"node_key": "bid.retrieval", "label": "标书检索就绪", "kind": "container", "sort_order": 25},
    {"node_key": "parse.bid", "label": "标书解析", "kind": "file", "parent_key": "bid.retrieval", "sort_order": 26},
    {"node_key": "index.segments", "label": "索引分段", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 27},
    {"node_key": "index.enrich", "label": "块增强", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 28},
    {"node_key": "index.fts", "label": "全文索引", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 29},
    {"node_key": "index.vectors", "label": "向量索引", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 30},
    {"node_key": "index.wiki", "label": "Wiki 构建", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 31},
    {"node_key": "index.gate", "label": "等待标书索引就绪", "kind": "gate", "parent_key": "bid.retrieval", "sort_order": 32},
    {"node_key": "interpret", "label": "招标文件解读", "kind": "stage", "sort_order": 90},
    {"node_key": "checklist.generate", "label": "检查项生成", "kind": "stage", "sort_order": 100},
    {"node_key": "diagnosis", "label": "标书诊断", "kind": "container", "sort_order": 110},
    {"node_key": "report.generate", "label": "报告生成", "kind": "stage", "sort_order": 120},
    {"node_key": "end", "label": "完成", "kind": "terminal", "sort_order": 130},
]

# Main graph (parent_key=null):
#   start → parse.tender → interpret → checklist.generate
#   start → bid.retrieval (container; sub-steps via parent_key)
# Runtime diagnosis.category.* fan in from checklist + index.gate, out to report.
TASK_GRAPH_EDGES: list[dict] = [
    {"from_key": "start", "to_key": "parse.tender", "edge_kind": "sequential"},
    {"from_key": "start", "to_key": "bid.retrieval", "edge_kind": "parallel"},
    {"from_key": "parse.tender", "to_key": "interpret", "edge_kind": "depends_on"},
    {"from_key": "interpret", "to_key": "checklist.generate", "edge_kind": "sequential"},
    {"from_key": "report.generate", "to_key": "end", "edge_kind": "sequential"},
]
