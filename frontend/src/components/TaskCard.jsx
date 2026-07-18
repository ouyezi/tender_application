import { useEffect, useRef, useState } from 'react'
import { reportDocxUrl } from '../api'

const STATUS_LABELS = {
  interpreting: '解读中',
  generating_checklist: '生成检查项',
  diagnosing: '诊断中',
  running: '诊断中',
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

export default function TaskCard({ task, onClick, onDelete, deleting }) {
  const status = task.status || 'running'
  const label = STATUS_LABELS[status] || status
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)

  useEffect(() => {
    if (!menuOpen) return
    function onDocClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [menuOpen])

  return (
    <article
      className={`task-card${deleting ? ' task-card-deleting' : ''}`}
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
        <div className="task-card-header-right">
          <time className="task-card-time" dateTime={task.created_at}>
            {formatDate(task.created_at)}
          </time>
          <div className="task-card-menu" ref={menuRef}>
            <button
              type="button"
              className="task-card-menu-trigger"
              aria-label="更多操作"
              aria-expanded={menuOpen}
              disabled={deleting}
              onClick={(e) => {
                e.stopPropagation()
                setMenuOpen((v) => !v)
              }}
            >
              ⋯
            </button>
            {menuOpen && (
              <div
                className="task-card-menu-dropdown"
                role="menu"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  type="button"
                  role="menuitem"
                  className="task-card-menu-item"
                  onClick={(e) => {
                    e.stopPropagation()
                    setMenuOpen(false)
                    onClick?.(task)
                  }}
                >
                  查看详情
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="task-card-menu-item task-card-menu-item-danger"
                  disabled={deleting}
                  onClick={(e) => {
                    e.stopPropagation()
                    setMenuOpen(false)
                    onDelete?.(task)
                  }}
                >
                  删除
                </button>
              </div>
            )}
          </div>
        </div>
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
