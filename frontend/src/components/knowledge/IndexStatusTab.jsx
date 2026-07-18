import { Link } from 'react-router-dom'

function pct(ratio) {
  if (ratio == null || Number.isNaN(Number(ratio))) return '—'
  return `${(Number(ratio) * 100).toFixed(1)}%`
}

function progressLabel(file) {
  const done = file.progress_done
  const total = file.progress_total
  if (done == null && total == null) return '—'
  return `${done ?? 0} / ${total ?? '—'}`
}

function rowClass(status) {
  if (status === 'failed' || status === 'partial') return `index-file-row index-file-${status}`
  return 'index-file-row'
}

export default function IndexStatusTab({ taskId, status, loading, error, onRefresh }) {
  const counts = status?.counts || {}
  const files = Array.isArray(status?.files) ? status.files : []

  return (
    <div className="index-status-tab">
      <div className="index-status-toolbar">
        <p className="empty-state-hint">
          与页头摘要同源；可刷新以获取最新索引进度。
        </p>
        {onRefresh && (
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={loading}
            onClick={onRefresh}
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
        )}
      </div>

      {error && <p className="page-error">{error}</p>}

      {loading && !status ? (
        <p className="empty-state-hint">加载索引状态…</p>
      ) : (
        <>
          <div className="index-summary knowledge-status-meta">
            <span>
              索引状态：<strong>{status?.index_status ?? '—'}</strong>
            </span>
            <span>
              incomplete：<strong>{status?.incomplete ? '是' : '否'}</strong>
            </span>
            <span>
              fine：<strong>{counts.fine ?? '—'}</strong>
            </span>
            <span>
              large：<strong>{counts.large ?? '—'}</strong>
            </span>
            <span>
              embedding 就绪：<strong>{pct(status?.embedding_ready_ratio)}</strong>
            </span>
            <span>
              FTS：<strong>{status?.fts_available ? '可用' : '不可用'}</strong>
            </span>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table index-files-table">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>status</th>
                  <th>stage</th>
                  <th>progress</th>
                  <th>error</th>
                </tr>
              </thead>
              <tbody>
                {files.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="empty-state-hint">
                      无索引任务记录
                    </td>
                  </tr>
                ) : (
                  files.map((file) => (
                    <tr key={file.file_id} className={rowClass(file.status)}>
                      <td>
                        <Link
                          to={`/workspaces/${taskId}?file_id=${encodeURIComponent(file.file_id)}`}
                        >
                          {file.label || file.file_id}
                        </Link>
                      </td>
                      <td>{file.status || '—'}</td>
                      <td>{file.stage || '—'}</td>
                      <td>{progressLabel(file)}</td>
                      <td className="index-file-error" title={file.error_message || ''}>
                        {file.error_message || '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <p className="empty-state-hint">
            <Link to={`/workspaces/${taskId}`}>打开工作区详情</Link>
          </p>
        </>
      )}
    </div>
  )
}
