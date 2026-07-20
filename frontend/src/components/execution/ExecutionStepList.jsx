import { NODE_STATUS_LABELS, formatDuration } from './executionFormatters.js'

export default function ExecutionStepList({ steps, selectedStepKey, onSelectStep }) {
  if (!steps.length) {
    return <p className="empty-state-hint">暂无子流程步骤</p>
  }

  return (
    <ol className="process-step-list">
      {steps.map((step, index) => {
        const status = step.status || 'pending'
        return (
          <li key={step.key} className="process-step-item">
            <button
              type="button"
              className={`process-step-button${selectedStepKey === step.key ? ' is-selected' : ''}`}
              onClick={() => onSelectStep(step.key)}
            >
              <span className={`process-step-dot node-status-${status}`} aria-hidden />
              <span className="process-step-label">{step.label}</span>
              <span className={`execution-node-status node-status-${status}`}>
                {NODE_STATUS_LABELS[status] || status}
              </span>
              <span className="process-step-duration">{formatDuration(step.duration_ms)}</span>
            </button>
            {index < steps.length - 1 ? (
              <span className="process-step-connector" aria-hidden />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
