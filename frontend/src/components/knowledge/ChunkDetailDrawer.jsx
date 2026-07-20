import { Link } from 'react-router-dom'

function tagLabel(tag) {
  if (!tag) return ''
  if (typeof tag === 'string') return tag
  const name = tag.name || ''
  if (tag.confidence == null || tag.confidence === '') return name
  const conf = Number(tag.confidence)
  if (Number.isNaN(conf)) return `${name} (${tag.confidence})`
  return `${name} (${conf.toFixed(2)})`
}

export default function ChunkDetailDrawer({
  taskId,
  chunk,
  loading,
  error,
  onClose,
  onOpenChunk,
}) {
  if (!loading && !chunk && !error) return null

  const titlePath = Array.isArray(chunk?.title_path) ? chunk.title_path : []
  const tags = Array.isArray(chunk?.tags) ? chunk.tags : []
  const childIds = Array.isArray(chunk?.child_chunk_ids) ? chunk.child_chunk_ids : []
  const fileId = chunk?.file_id
  const nodeId = chunk?.node_id
  const readerHref =
    taskId && fileId
      ? `/workspaces/${taskId}?file_id=${encodeURIComponent(fileId)}${
          nodeId ? `&node_id=${encodeURIComponent(nodeId)}` : ''
        }`
      : null
  const hasContextMeta =
    chunk?.context_role || chunk?.derived_from || chunk?.anchor_chunk_id

  return (
    <div className="chunk-drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        className="chunk-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="知识块详情"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="chunk-drawer-header">
          <h2>{chunk?.title || (loading ? '加载中…' : '知识块详情')}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="chunk-drawer-body">
          {loading && <p className="empty-state-hint">加载详情…</p>}
          {error && <p className="page-error">{error}</p>}
          {!loading && chunk && (
            <>
              <div className="chunk-drawer-meta">
                <span>
                  粒度：<strong>{chunk.segment_level || '—'}</strong>
                </span>
                <span>
                  来源：<strong>{chunk.source || '—'}</strong>
                </span>
                <span>
                  索引：<strong>{chunk.index_status || '—'}</strong>
                </span>
                <span>
                  向量：<strong>{chunk.embedding_status || '—'}</strong>
                </span>
                <span className="chunk-drawer-id" title={chunk.chunk_id}>
                  ID：<code>{chunk.chunk_id}</code>
                </span>
              </div>

              {hasContextMeta && (
                <section className="chunk-drawer-section">
                  <h3>上下文解析</h3>
                  <div className="chunk-drawer-meta">
                    <span>
                      context_role：<strong>{chunk.context_role || 'matched'}</strong>
                    </span>
                    {chunk.derived_from ? (
                      <span>
                        derived_from：<code>{chunk.derived_from}</code>
                      </span>
                    ) : null}
                    {chunk.anchor_chunk_id ? (
                      <span>
                        anchor_chunk_id：<code>{chunk.anchor_chunk_id}</code>
                      </span>
                    ) : null}
                  </div>
                </section>
              )}

              <section className="chunk-drawer-section">
                <h3>标题路径</h3>
                <p className="chunk-drawer-path">
                  {titlePath.length > 0 ? titlePath.join(' / ') : '—'}
                </p>
              </section>

              <section className="chunk-drawer-section">
                <h3>概要</h3>
                <p>{chunk.summary || '—'}</p>
              </section>

              <section className="chunk-drawer-section">
                <h3>描述</h3>
                <p>{chunk.description || '—'}</p>
              </section>

              <section className="chunk-drawer-section">
                <h3>标签</h3>
                {tags.length === 0 ? (
                  <p className="empty-state-hint">无标签</p>
                ) : (
                  <div className="chunk-tag-list">
                    {tags.map((tag, idx) => (
                      <span key={`${tagLabel(tag)}-${idx}`} className="chunk-tag">
                        {tagLabel(tag)}
                      </span>
                    ))}
                  </div>
                )}
              </section>

              {childIds.length > 0 && (
                <section className="chunk-drawer-section">
                  <h3>子知识块</h3>
                  <ul className="chunk-child-list">
                    {childIds.map((id) => (
                      <li key={id}>
                        <button
                          type="button"
                          className="chunk-child-link"
                          onClick={() => onOpenChunk?.(id)}
                        >
                          {id}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section className="chunk-drawer-section">
                <h3>
                  正文
                  {chunk.text_truncated ? (
                    <span className="chunk-degraded-badge">已截断</span>
                  ) : null}
                </h3>
                <pre className="chunk-text-preview">{chunk.text || '—'}</pre>
              </section>

              {readerHref && (
                <div className="chunk-drawer-actions">
                  <Link className="btn btn-secondary" to={readerHref}>
                    打开阅读器
                  </Link>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  )
}
