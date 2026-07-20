import { Handle, Position } from '@xyflow/react'
import { NODE_STATUS_LABELS, formatDuration } from './executionFormatters.js'

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
          {NODE_STATUS_LABELS[status] || status}
        </span>
        <span className="execution-node-duration">{formatDuration(data.duration_ms)}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
