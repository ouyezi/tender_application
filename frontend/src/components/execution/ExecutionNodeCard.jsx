import { Handle, Position } from '@xyflow/react'

const STATUS_LABELS = {
  pending: '等待',
  running: '运行中',
  completed: '完成',
  failed: '失败',
  interrupted: '中断',
  skipped: '跳过',
}

function formatDuration(ms) {
  if (ms == null || ms <= 0) return '—'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rem = seconds % 60
  return rem ? `${minutes}m ${rem}s` : `${minutes}m`
}

export default function ExecutionNodeCard({ data, selected }) {
  const status = data.status || 'pending'
  const isSelected = data.selected || selected

  return (
    <div
      className={`execution-node node-${status}${isSelected ? ' execution-node-selected' : ''}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="execution-node-label">{data.label}</div>
      <div className="execution-node-meta">
        <span className={`execution-node-status node-status-${status}`}>
          {STATUS_LABELS[status] || status}
        </span>
        <span className="execution-node-duration">{formatDuration(data.duration_ms)}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
