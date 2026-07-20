export const NODE_STATUS_LABELS = {
  pending: '等待',
  running: '运行中',
  completed: '完成',
  failed: '失败',
  interrupted: '中断',
  skipped: '跳过',
}

export function formatDuration(ms) {
  if (ms == null || ms <= 0) return '—'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rem = seconds % 60
  return rem ? `${minutes}m ${rem}s` : `${minutes}m`
}

export const BID_RETRIEVAL_STEP_ORDER = [
  'parse.bid',
  'index.segments',
  'index.enrich',
  'index.fts',
  'index.vectors',
  'index.wiki',
  'index.gate',
]

export function sortExecutionSteps(steps, fallbackOrder = BID_RETRIEVAL_STEP_ORDER) {
  const orderIndex = Object.fromEntries(fallbackOrder.map((key, index) => [key, index]))
  return [...steps].sort((a, b) => {
    const aOrder = a.sort_order ?? orderIndex[a.key] ?? 999
    const bOrder = b.sort_order ?? orderIndex[b.key] ?? 999
    if (aOrder !== bOrder) return aOrder - bOrder
    return a.key.localeCompare(b.key)
  })
}
