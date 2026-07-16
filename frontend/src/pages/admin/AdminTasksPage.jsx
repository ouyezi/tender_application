import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listTasks, pauseTask, resumeTask, stopTask } from '../../api'

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
      second: '2-digit',
    })
  } catch {
    return String(value)
  }
}

function progressPercent(done, total) {
  if (!total || total <= 0) return 0
  return Math.min(100, Math.round((done / total) * 100))
}

export default function AdminTasksPage() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState(null)

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const data = await listTasks()
      setTasks(Array.isArray(data) ? data : [])
      setError('')
    } catch (err) {
      setError(err.message || '加载任务列表失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(() => refresh(true), 2000)
    return () => clearInterval(timer)
  }, [refresh])

  async function runControl(taskId, action, fn) {
    setBusyId(taskId)
    try {
      await fn(taskId)
      await refresh(true)
    } catch (err) {
      window.alert(`${action}失败：${err.message || '未知错误'}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <main className="page admin-page">
      <header className="page-header">
        <h1>诊断任务</h1>
      </header>

      {error && <p className="page-error">{error}</p>}

      {loading && tasks.length === 0 ? (
        <p className="empty-state">加载中…</p>
      ) : tasks.length === 0 ? (
        <div className="empty-state">
          <p>暂无诊断任务</p>
          <p className="empty-state-hint">
            请到 <Link to="/">任务列表</Link> 创建诊断任务
          </p>
        </div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table admin-tasks-table">
            <thead>
              <tr>
                <th>任务 ID</th>
                <th>状态</th>
                <th>招标 / 投标文件</th>
                <th>创建时间</th>
                <th>进度</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => {
                const status = task.status || 'running'
                const done = task.progress_done ?? 0
                const total = task.progress_total ?? 0
                const pct = progressPercent(done, total)
                const busy = busyId === task.id

                return (
                  <tr key={task.id}>
                    <td>
                      <Link className="admin-task-id" to={`/tasks/${task.id}`}>
                        <code>{task.id}</code>
                      </Link>
                    </td>
                    <td>
                      <span className={`status-badge status-${status}`}>
                        {STATUS_LABELS[status] || status}
                      </span>
                    </td>
                    <td>
                      <div className="admin-task-files">
                        <span title={task.tender_filename}>
                          {task.tender_filename || '—'}
                        </span>
                        <span title={task.bid_filename}>
                          {task.bid_filename || '—'}
                        </span>
                      </div>
                    </td>
                    <td className="admin-task-time">
                      {formatDate(task.created_at)}
                    </td>
                    <td>
                      <div className="progress-cell">
                        <div
                          className="progress-bar"
                          role="progressbar"
                          aria-valuenow={pct}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-label={`进度 ${done}/${total}`}
                        >
                          <div
                            className="progress-bar-fill"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="progress-text">
                          {done}/{total}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="admin-table-actions">
                        {(status === 'diagnosing' || status === 'running') && (
                          <>
                            <button
                              type="button"
                              className="btn btn-sm btn-secondary"
                              disabled={busy}
                              onClick={() =>
                                runControl(task.id, '暂停', pauseTask)
                              }
                            >
                              暂停
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm btn-danger"
                              disabled={busy}
                              onClick={() =>
                                runControl(task.id, '停止', stopTask)
                              }
                            >
                              停止
                            </button>
                          </>
                        )}
                        {status === 'interpreting' && (
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            disabled={busy}
                            onClick={() =>
                              runControl(task.id, '停止', stopTask)
                            }
                          >
                            停止
                          </button>
                        )}
                        {status === 'paused' && (
                          <>
                            <button
                              type="button"
                              className="btn btn-sm btn-primary"
                              disabled={busy}
                              onClick={() =>
                                runControl(task.id, '继续', resumeTask)
                              }
                            >
                              继续
                            </button>
                            <button
                              type="button"
                              className="btn btn-sm btn-danger"
                              disabled={busy}
                              onClick={() =>
                                runControl(task.id, '停止', stopTask)
                              }
                            >
                              停止
                            </button>
                          </>
                        )}
                        {(status === 'completed' ||
                          status === 'stopped' ||
                          status === 'failed') && (
                          <span className="admin-task-no-actions">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  )
}
