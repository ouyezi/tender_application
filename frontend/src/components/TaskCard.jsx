import { reportDocxUrl } from '../api'

const STATUS_LABELS = {
  interpreting: '解读中',
  diagnosing: '诊断中',
  running: '诊断中', // legacy
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return String(value)
  }
}

export default function TaskCard({ task, onClick }) {
  const status = task.status || 'running'
  const label = STATUS_LABELS[status] || status

  return (
    <article
      className="task-card"
      role="button"
      tabIndex={0}
      onClick={() => onClick?.(task)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick?.(task)
        }
      }}
    >
      <div className="task-card-header">
        <span className={`status-badge status-${status}`}>{label}</span>
        <time className="task-card-time" dateTime={task.created_at}>
          {formatDate(task.created_at)}
        </time>
      </div>

      <div className="task-card-files">
        <div className="task-card-file">
          <span className="task-card-label">招标文件</span>
          <span className="task-card-name" title={task.tender_filename}>
            {task.tender_filename || '—'}
          </span>
        </div>
        <div className="task-card-file">
          <span className="task-card-label">投标文件</span>
          <span className="task-card-name" title={task.bid_filename}>
            {task.bid_filename || '—'}
          </span>
        </div>
      </div>

      <div className="task-card-footer">
        <code className="task-card-id">{task.id}</code>
        {status === 'completed' && (
          <a
            className="task-card-download"
            href={reportDocxUrl(task.id)}
            onClick={(e) => e.stopPropagation()}
          >
            下载报告
          </a>
        )}
      </div>
    </article>
  )
}
