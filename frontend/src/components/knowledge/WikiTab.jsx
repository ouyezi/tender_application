import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getKnowledgeWiki, getKnowledgeWikiPage } from '../../api'

function formatTags(tags) {
  const list = Array.isArray(tags) ? tags : []
  const names = list.map((t) => (typeof t === 'string' ? t : t?.name)).filter(Boolean)
  return names.length ? names.join(', ') : '—'
}

export default function WikiTab({ taskId }) {
  const [, setSearchParams] = useSearchParams()

  const [pages, setPages] = useState([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState('')

  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  const loadList = useCallback(async () => {
    if (!taskId) return
    setListLoading(true)
    try {
      const data = await getKnowledgeWiki(taskId)
      setPages(Array.isArray(data) ? data : [])
      setListError('')
    } catch (err) {
      setPages([])
      setListError(err.message || '加载 Wiki 列表失败')
    } finally {
      setListLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    loadList()
  }, [loadList])

  const openPage = useCallback(
    async (wikiId) => {
      if (!taskId || wikiId == null) return
      setSelectedId(wikiId)
      setDetailLoading(true)
      setDetailError('')
      try {
        const data = await getKnowledgeWikiPage(taskId, wikiId)
        setDetail(data)
      } catch (err) {
        setDetail(null)
        setDetailError(err.message || '加载 Wiki 详情失败')
      } finally {
        setDetailLoading(false)
      }
    },
    [taskId],
  )

  function openChunk(chunkId) {
    setSearchParams({ tab: 'chunks', chunk_id: chunkId })
  }

  const members = Array.isArray(detail?.member_summaries) ? detail.member_summaries : []

  return (
    <div className="wiki-tab">
      <p className="wiki-tip">
        权威召回以标签过滤为准；请在「检索试跑」用 collection 对照成员列表。
      </p>

      {listError && <p className="page-error">{listError}</p>}

      <div className="wiki-layout">
        <div className="wiki-list-panel">
          <h3>Wiki 页面</h3>
          {listLoading ? (
            <p className="empty-state-hint">加载中…</p>
          ) : pages.length === 0 ? (
            <p className="empty-state-hint">暂无 Wiki 页面</p>
          ) : (
            <ul className="wiki-list">
              {pages.map((page) => (
                <li key={page.wiki_id}>
                  <button
                    type="button"
                    className={
                      selectedId === page.wiki_id ? 'wiki-list-item active' : 'wiki-list-item'
                    }
                    onClick={() => openPage(page.wiki_id)}
                  >
                    <span className="wiki-list-title">{page.title || `Wiki #${page.wiki_id}`}</span>
                    <span className="wiki-list-meta">
                      {(page.member_chunk_ids || []).length} 成员 · {formatTags(page.tags)}
                      {page.updated_at ? ` · ${page.updated_at}` : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="wiki-detail-panel">
          <h3>详情</h3>
          {!selectedId && !detailLoading && (
            <p className="empty-state-hint">选择左侧页面查看详情</p>
          )}
          {detailLoading && <p className="empty-state-hint">加载详情…</p>}
          {detailError && <p className="page-error">{detailError}</p>}
          {!detailLoading && detail && (
            <div className="wiki-detail">
              <h2>{detail.title || `Wiki #${detail.wiki_id}`}</h2>
              <div className="wiki-detail-meta">
                <span>
                  标签：<strong>{formatTags(detail.tags)}</strong>
                </span>
                <span>
                  成员数：<strong>{(detail.member_chunk_ids || []).length}</strong>
                </span>
              </div>

              <section className="wiki-section">
                <h4>摘要</h4>
                <p>{detail.summary || '—'}</p>
              </section>

              <section className="wiki-section">
                <h4>描述</h4>
                <p>{detail.description || '—'}</p>
              </section>

              <section className="wiki-section">
                <h4>成员</h4>
                {members.length === 0 ? (
                  <p className="empty-state-hint">无成员块</p>
                ) : (
                  <div className="wiki-member-grid">
                    {members.map((m) => (
                      <button
                        key={m.chunk_id}
                        type="button"
                        className="wiki-member-card"
                        onClick={() => openChunk(m.chunk_id)}
                        title={m.chunk_id}
                      >
                        <span className="wiki-member-title">{m.title || m.chunk_id}</span>
                        {m.summary && (
                          <span className="wiki-member-summary">{m.summary}</span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
