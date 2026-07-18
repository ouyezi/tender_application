import { useCallback, useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { getKnowledgeIndexStatus } from '../api'
import ChunksTab from '../components/knowledge/ChunksTab'
import RetrieveTab from '../components/knowledge/RetrieveTab'

const TABS = [
  { id: 'chunks', label: '知识块' },
  { id: 'retrieve', label: '检索调试' },
  { id: 'wiki', label: 'Wiki' },
  { id: 'index', label: '索引状态' },
]

const VALID_TABS = new Set(TABS.map((t) => t.id))

function WikiTab() {
  return <p>Wiki</p>
}

function IndexTab() {
  return <p>索引状态</p>
}

export default function KnowledgeDebugPage() {
  const { taskId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const rawTab = searchParams.get('tab') || 'chunks'
  const tab = VALID_TABS.has(rawTab) ? rawTab : 'chunks'

  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadStatus = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const data = await getKnowledgeIndexStatus(taskId)
      setStatus(data)
      setError('')
    } catch (err) {
      setError(err.message || '加载索引状态失败')
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const setTab = (next) => {
    setSearchParams((prev) => {
      const nextParams = new URLSearchParams(prev)
      nextParams.set('tab', next)
      if (next !== 'chunks') nextParams.delete('chunk_id')
      return nextParams
    })
  }

  const counts = status?.counts || {}
  const fine = counts.fine ?? '—'
  const large = counts.large ?? '—'

  return (
    <main className="page knowledge-debug-page">
      <header className="page-header">
        <div className="page-header-titles">
          <Link className="back-link" to={`/workspaces/${taskId}`}>
            ← 返回工作区
          </Link>
          <h1>知识检索</h1>
        </div>
        <div className="page-header-actions">
          <Link className="btn btn-secondary" to={`/workspaces/${taskId}`}>
            工作区详情
          </Link>
        </div>
      </header>

      {error && <p className="page-error">{error}</p>}

      <section className="detail-section knowledge-status-bar">
        {loading && !status ? (
          <p className="empty-state">加载索引状态…</p>
        ) : (
          <div className="knowledge-status-meta">
            <span>
              索引状态：<strong>{status?.index_status ?? '—'}</strong>
            </span>
            <span>
              fine：<strong>{fine}</strong>
            </span>
            <span>
              large：<strong>{large}</strong>
            </span>
            <span>
              incomplete：<strong>{status?.incomplete ? '是' : '否'}</strong>
            </span>
          </div>
        )}
      </section>

      <div className="knowledge-tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? 'knowledge-tab active' : 'knowledge-tab'}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <section className="detail-section knowledge-tab-panel">
        {tab === 'chunks' && <ChunksTab taskId={taskId} />}
        {tab === 'retrieve' && <RetrieveTab taskId={taskId} />}
        {tab === 'wiki' && <WikiTab />}
        {tab === 'index' && <IndexTab />}
      </section>
    </main>
  )
}
