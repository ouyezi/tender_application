import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { listWorkspaces } from '../api'

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

function parseSummary(item) {
  const succeeded = item.parse_succeeded ?? 0
  const running = item.parse_running ?? 0
  const failed = item.parse_failed ?? 0
  return { succeeded, running, failed }
}

export default function WorkspaceListPage() {
  const navigate = useNavigate()
  const [workspaces, setWorkspaces] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const data = await listWorkspaces()
      setWorkspaces(Array.isArray(data) ? data : [])
      setError('')
    } catch (err) {
      setError(err.message || '加载工作区列表失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(() => refresh(true), 3000)
    return () => clearInterval(timer)
  }, [refresh])

  return (
    <main className="page workspace-list-page">
      <header className="page-header">
        <div className="page-header-titles">
          <h1>工作区</h1>
          <Link className="header-link" to="/">
            诊断
          </Link>
          <Link className="header-link" to="/admin">
            管理后台
          </Link>
        </div>
      </header>

      {error && <p className="page-error">{error}</p>}

      {loading && workspaces.length === 0 ? (
        <p className="empty-state">加载中…</p>
      ) : workspaces.length === 0 ? (
        <div className="empty-state">
          <p>暂无工作区</p>
          <p className="empty-state-hint">
            创建诊断任务后会自动注册对应工作区
          </p>
        </div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table workspace-list-table">
            <thead>
              <tr>
                <th>任务 ID</th>
                <th>招标 / 投标文件</th>
                <th>文件数</th>
                <th>解析状态</th>
                <th>创建时间</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.map((item) => {
                const { succeeded, running, failed } = parseSummary(item)

                return (
                  <tr
                    key={item.task_id}
                    className="workspace-list-row"
                    onClick={() => navigate(`/workspaces/${item.task_id}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/workspaces/${item.task_id}`)
                      }
                    }}
                    tabIndex={0}
                    role="link"
                    aria-label={`打开工作区 ${item.task_id}`}
                  >
                    <td>
                      <code className="workspace-task-id">{item.task_id}</code>
                    </td>
                    <td>
                      <div className="admin-task-files">
                        <span title={item.tender_filename}>
                          {item.tender_filename || '—'}
                        </span>
                        <span title={item.bid_filename}>
                          {item.bid_filename || '—'}
                        </span>
                      </div>
                    </td>
                    <td className="workspace-file-count">{item.file_count ?? 0}</td>
                    <td>
                      <div className="workspace-parse-summary">
                        <span className="parse-stat parse-stat-succeeded" title="解析成功">
                          {succeeded} 成功
                        </span>
                        {running > 0 && (
                          <span className="parse-stat parse-stat-running" title="解析中">
                            {running} 进行中
                          </span>
                        )}
                        {failed > 0 && (
                          <span className="parse-stat parse-stat-failed" title="解析失败">
                            {failed} 失败
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="admin-task-time">{formatDate(item.created_at)}</td>
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
