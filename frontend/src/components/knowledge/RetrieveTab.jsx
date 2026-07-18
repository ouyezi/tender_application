import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { debugKnowledgeRetrieve, getKnowledgeTags } from '../../api'

const SOURCES = [
  { id: 'full_document', label: 'full_document' },
  { id: 'collection', label: 'collection' },
  { id: 'large_segments', label: 'large_segments' },
  { id: 'precise_search', label: 'precise_search' },
]

function pathTail(titlePath, n = 2) {
  const arr = Array.isArray(titlePath) ? titlePath : []
  if (arr.length === 0) return '—'
  return arr.slice(-n).join(' / ')
}

function formatApiError(err) {
  const text = err?.message || '请求失败'
  try {
    const data = JSON.parse(text)
    const detail = data.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      const msg = detail.message || text
      if (Array.isArray(detail.allowed_tags) && detail.allowed_tags.length) {
        return `${msg}（允许标签：${detail.allowed_tags.join(', ')}）`
      }
      return msg
    }
  } catch {
    /* not JSON */
  }
  return text
}

function buildRequestBody({
  contentSource,
  fileRole,
  targetTags,
  rootNodeId,
  query,
  hintsText,
}) {
  const content_target = {}
  let item_hints = null

  if (contentSource === 'full_document') {
    content_target.file_role = fileRole || 'tender'
  } else if (contentSource === 'collection') {
    content_target.target_tags = targetTags
  } else if (contentSource === 'large_segments') {
    content_target.file_role = fileRole || 'tender'
    if (rootNodeId.trim()) content_target.root_node_id = rootNodeId.trim()
  } else if (contentSource === 'precise_search') {
    content_target.query = query.trim()
    const hints = hintsText
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (hints.length) item_hints = { retrieval_hints: hints }
  }

  return {
    content_source: contentSource,
    content_target,
    item_hints,
  }
}

function multiChannelIds(channels) {
  const counts = new Map()
  for (const list of Object.values(channels || {})) {
    for (const hit of list || []) {
      const id = hit?.chunk_id
      if (!id) continue
      counts.set(id, (counts.get(id) || 0) + 1)
    }
  }
  return new Set([...counts.entries()].filter(([, n]) => n > 1).map(([id]) => id))
}

function JsonBlock({ value }) {
  return <pre className="retrieve-json">{JSON.stringify(value, null, 2)}</pre>
}

function ChannelTable({ name, hits, multiIds }) {
  const rows = Array.isArray(hits) ? hits : []
  return (
    <div className="retrieve-channel">
      <h4>{name}</h4>
      {rows.length === 0 ? (
        <p className="empty-state-hint">无命中</p>
      ) : (
        <table className="admin-table retrieve-mini-table">
          <thead>
            <tr>
              <th>chunk_id</th>
              <th>score</th>
              <th>title</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((hit) => (
              <tr
                key={`${name}-${hit.chunk_id}`}
                className={multiIds.has(hit.chunk_id) ? 'retrieve-multi-hit' : ''}
              >
                <td>
                  <code>{hit.chunk_id}</code>
                </td>
                <td>{hit.score != null ? Number(hit.score).toFixed(4) : '—'}</td>
                <td>{hit.title || '—'}</td>
                <td>
                  {multiIds.has(hit.chunk_id) && (
                    <span className="retrieve-multi-badge">多路</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export default function RetrieveTab({ taskId }) {
  const [, setSearchParams] = useSearchParams()

  const [contentSource, setContentSource] = useState('precise_search')
  const [fileRole, setFileRole] = useState('tender')
  const [targetTags, setTargetTags] = useState([])
  const [rootNodeId, setRootNodeId] = useState('')
  const [query, setQuery] = useState('')
  const [hintsText, setHintsText] = useState('')

  const [tagOptions, setTagOptions] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [lastRequest, setLastRequest] = useState(null)
  const [response, setResponse] = useState(null)
  const [copyMsg, setCopyMsg] = useState('')

  useEffect(() => {
    if (!taskId) return undefined
    let cancelled = false
    ;(async () => {
      try {
        const list = await getKnowledgeTags(taskId)
        if (!cancelled) setTagOptions(Array.isArray(list) ? list : [])
      } catch {
        if (!cancelled) setTagOptions([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [taskId])

  function toggleTag(name) {
    setTargetTags((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name],
    )
  }

  function openInChunks(chunkId) {
    setSearchParams({ tab: 'chunks', chunk_id: chunkId })
  }

  async function handleRun(e) {
    e.preventDefault()
    if (!taskId) return
    const body = buildRequestBody({
      contentSource,
      fileRole,
      targetTags,
      rootNodeId,
      query,
      hintsText,
    })
    setLoading(true)
    setError('')
    setCopyMsg('')
    setLastRequest(body)
    try {
      const data = await debugKnowledgeRetrieve(taskId, body)
      setResponse(data)
    } catch (err) {
      setResponse(null)
      setError(formatApiError(err))
    } finally {
      setLoading(false)
    }
  }

  async function copyJson() {
    if (!lastRequest && !response) return
    const payload = JSON.stringify({ request: lastRequest, response }, null, 2)
    try {
      await navigator.clipboard.writeText(payload)
      setCopyMsg('已复制')
    } catch {
      setCopyMsg('复制失败')
    }
  }

  const items = Array.isArray(response?.items) ? response.items : []
  const trace = response?.trace
  const channels = trace?.channels || {}
  const multiIds = multiChannelIds(channels)
  const skipped = Array.isArray(trace?.skipped_stages) ? trace.skipped_stages : []

  return (
    <div className="retrieve-tab">
      <form className="retrieve-form" onSubmit={handleRun}>
        <fieldset className="retrieve-fieldset">
          <legend>content_source</legend>
          <div className="retrieve-source-options">
            {SOURCES.map((s) => (
              <label key={s.id} className="retrieve-radio">
                <input
                  type="radio"
                  name="content_source"
                  value={s.id}
                  checked={contentSource === s.id}
                  onChange={() => setContentSource(s.id)}
                />
                {s.label}
              </label>
            ))}
          </div>
        </fieldset>

        {(contentSource === 'full_document' || contentSource === 'large_segments') && (
          <label className="field">
            file_role
            <select value={fileRole} onChange={(e) => setFileRole(e.target.value)}>
              <option value="tender">tender</option>
              <option value="bid">bid</option>
            </select>
          </label>
        )}

        {contentSource === 'large_segments' && (
          <label className="field">
            root_node_id（可选）
            <input
              type="text"
              value={rootNodeId}
              onChange={(e) => setRootNodeId(e.target.value)}
              placeholder="节点 ID"
            />
          </label>
        )}

        {contentSource === 'collection' && (
          <fieldset className="retrieve-fieldset">
            <legend>target_tags</legend>
            {tagOptions.length === 0 ? (
              <p className="empty-state-hint">暂无可用标签</p>
            ) : (
              <div className="retrieve-tag-checks">
                {tagOptions.map((t) => (
                  <label key={t.name} className="retrieve-check">
                    <input
                      type="checkbox"
                      checked={targetTags.includes(t.name)}
                      onChange={() => toggleTag(t.name)}
                    />
                    {t.name}
                  </label>
                ))}
              </div>
            )}
          </fieldset>
        )}

        {contentSource === 'precise_search' && (
          <>
            <label className="field">
              query
              <textarea
                rows={3}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="检索查询"
              />
            </label>
            <label className="field">
              retrieval_hints（可选，逗号分隔）
              <input
                type="text"
                value={hintsText}
                onChange={(e) => setHintsText(e.target.value)}
                placeholder="hint1, hint2"
              />
            </label>
          </>
        )}

        <div className="retrieve-form-actions">
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? '运行中…' : '运行'}
          </button>
        </div>
      </form>

      {error && <p className="page-error">{error}</p>}

      {response && (
        <div className="retrieve-results">
          <div className="retrieve-status-bar knowledge-status-meta">
            <span>
              mode：<strong>{response.mode ?? '—'}</strong>
            </span>
            <span>
              index_status：<strong>{response.index_status ?? '—'}</strong>
            </span>
            <span>
              incomplete：<strong>{response.incomplete ? '是' : '否'}</strong>
            </span>
            <span>
              degraded：<strong>{response.degraded ? '是' : '否'}</strong>
            </span>
            <span>
              error：<strong>{response.error || '—'}</strong>
            </span>
          </div>

          {response.path_note && (
            <p className="retrieve-path-note">{response.path_note}</p>
          )}

          <div className="retrieve-hits-header">
            <h3>命中结果（{items.length}）</h3>
            <div className="retrieve-hits-actions">
              <button type="button" className="btn btn-sm btn-secondary" onClick={copyJson}>
                复制 JSON
              </button>
              {copyMsg && <span className="empty-state-hint">{copyMsg}</span>}
            </div>
          </div>

          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>score</th>
                  <th>level</th>
                  <th>title</th>
                  <th>path</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="empty-state-hint">
                      无命中
                    </td>
                  </tr>
                ) : (
                  items.map((item) => (
                    <tr key={item.chunk_id}>
                      <td>{item.score != null ? Number(item.score).toFixed(4) : '—'}</td>
                      <td>
                        <span
                          className={`chunk-level-badge chunk-level-${item.segment_level || ''}`}
                        >
                          {item.segment_level || '—'}
                        </span>
                      </td>
                      <td title={item.title}>{item.title || '—'}</td>
                      <td title={(item.title_path || []).join(' / ')}>
                        {pathTail(item.title_path)}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-sm btn-secondary"
                          onClick={() => openInChunks(item.chunk_id)}
                        >
                          在知识块中打开
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {trace && (
            <div className="retrieve-trace">
              <h3>Trace</h3>
              {skipped.length > 0 && (
                <p className="empty-state-hint">
                  skipped_stages：{skipped.join(', ')}
                </p>
              )}

              <details className="retrieve-details" open={contentSource === 'precise_search'}>
                <summary>查询重写</summary>
                <JsonBlock value={trace.rewrite || {}} />
              </details>

              <details className="retrieve-details">
                <summary>三路召回（多路命中已标记）</summary>
                <div className="retrieve-channels">
                  <ChannelTable name="vector" hits={channels.vector} multiIds={multiIds} />
                  <ChannelTable name="keyword" hits={channels.keyword} multiIds={multiIds} />
                  <ChannelTable name="wiki" hits={channels.wiki} multiIds={multiIds} />
                </div>
              </details>

              <details className="retrieve-details">
                <summary>merged（score + channel_flags）</summary>
                {(trace.merged || []).length === 0 ? (
                  <p className="empty-state-hint">无</p>
                ) : (
                  <table className="admin-table retrieve-mini-table">
                    <thead>
                      <tr>
                        <th>chunk_id</th>
                        <th>score</th>
                        <th>vector</th>
                        <th>keyword</th>
                        <th>wiki</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(trace.merged || []).map((row) => (
                        <tr key={row.chunk_id}>
                          <td>
                            <code>{row.chunk_id}</code>
                          </td>
                          <td>{row.score != null ? Number(row.score).toFixed(4) : '—'}</td>
                          <td>{row.channel_flags?.vector ? '✓' : '—'}</td>
                          <td>{row.channel_flags?.keyword ? '✓' : '—'}</td>
                          <td>{row.channel_flags?.wiki ? '✓' : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </details>

              <details className="retrieve-details">
                <summary>pre vs post 重排顺序</summary>
                <div className="retrieve-order-grid">
                  <div>
                    <h4>pre_rerank_order</h4>
                    <ol className="retrieve-order-list">
                      {(trace.pre_rerank_order || []).map((id) => (
                        <li key={`pre-${id}`}>
                          <code>{id}</code>
                        </li>
                      ))}
                    </ol>
                    {(trace.pre_rerank_order || []).length === 0 && (
                      <p className="empty-state-hint">空</p>
                    )}
                  </div>
                  <div>
                    <h4>post_rerank_order</h4>
                    <ol className="retrieve-order-list">
                      {(trace.post_rerank_order || []).map((id) => (
                        <li key={`post-${id}`}>
                          <code>{id}</code>
                        </li>
                      ))}
                    </ol>
                    {(trace.post_rerank_order || []).length === 0 && (
                      <p className="empty-state-hint">空</p>
                    )}
                  </div>
                </div>
              </details>

              <details className="retrieve-details">
                <summary>AI ranks / degraded_reason / rationale</summary>
                <JsonBlock value={trace.ai_rerank || {}} />
              </details>

              <details className="retrieve-details">
                <summary>expansions</summary>
                {(trace.expansions || []).length === 0 ? (
                  <p className="empty-state-hint">无扩展</p>
                ) : (
                  <table className="admin-table retrieve-mini-table">
                    <thead>
                      <tr>
                        <th>from_fine</th>
                        <th>to_large</th>
                        <th>reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(trace.expansions || []).map((ex, i) => (
                        <tr key={`${ex.from_fine_id}-${i}`}>
                          <td>
                            <code>{ex.from_fine_id}</code>
                          </td>
                          <td>
                            <code>{ex.to_large_id}</code>
                          </td>
                          <td>{ex.reason || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </details>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
