import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  getKnowledgeChunk,
  getKnowledgeChunks,
  getKnowledgeTags,
  getWorkspace,
  getWorkspaceTree,
} from '../../api'
import DocumentTree from '../DocumentTree'
import ChunkDetailDrawer from './ChunkDetailDrawer'

const PAGE_SIZE = 20

function pathTail(titlePath, n = 2) {
  const arr = Array.isArray(titlePath) ? titlePath : []
  if (arr.length === 0) return '—'
  return arr.slice(-n).join(' / ')
}

function formatTags(tags, limit = 3) {
  const list = Array.isArray(tags) ? tags : []
  const names = list.map((t) => (typeof t === 'string' ? t : t?.name)).filter(Boolean)
  if (names.length === 0) return '—'
  const shown = names.slice(0, limit).join(', ')
  const extra = names.length - limit
  return extra > 0 ? `${shown} +${extra}` : shown
}

function extractNodes(data) {
  if (Array.isArray(data)) return data
  if (Array.isArray(data?.nodes)) return data.nodes
  return []
}

export default function ChunksTab({ taskId }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const chunkId = searchParams.get('chunk_id') || ''

  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [segmentLevel, setSegmentLevel] = useState('')
  const [tag, setTag] = useState('')
  const [source, setSource] = useState('')
  const [indexStatus, setIndexStatus] = useState('')
  const [fileId, setFileId] = useState('')
  const [page, setPage] = useState(1)

  const [useTreeFilter, setUseTreeFilter] = useState(false)
  const [treeFileId, setTreeFileId] = useState('')
  const [treeNodes, setTreeNodes] = useState([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeError, setTreeError] = useState('')
  const [nodeId, setNodeId] = useState('')

  const [tags, setTags] = useState([])
  const [files, setFiles] = useState([])
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [searchDegraded, setSearchDegraded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(qInput.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(timer)
  }, [qInput])

  useEffect(() => {
    if (!taskId) return undefined
    let cancelled = false
    ;(async () => {
      try {
        const [tagList, workspace] = await Promise.all([
          getKnowledgeTags(taskId),
          getWorkspace(taskId),
        ])
        if (cancelled) return
        setTags(Array.isArray(tagList) ? tagList : [])
        setFiles(Array.isArray(workspace?.files) ? workspace.files : [])
      } catch {
        if (!cancelled) {
          setTags([])
          setFiles([])
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [taskId])

  useEffect(() => {
    if (!useTreeFilter || !taskId || !treeFileId) {
      setTreeNodes([])
      setTreeError('')
      setTreeLoading(false)
      return undefined
    }
    let cancelled = false
    setTreeLoading(true)
    setTreeError('')
    ;(async () => {
      try {
        const data = await getWorkspaceTree(taskId, treeFileId)
        if (cancelled) return
        setTreeNodes(extractNodes(data))
      } catch (err) {
        if (!cancelled) {
          setTreeNodes([])
          setTreeError(err.message || '加载目录失败')
        }
      } finally {
        if (!cancelled) setTreeLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [useTreeFilter, taskId, treeFileId])

  const loadChunks = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const params = {
        q: q || undefined,
        segment_level: segmentLevel || undefined,
        tag: tag || undefined,
        source: source || undefined,
        index_status: indexStatus || undefined,
        file_id: (useTreeFilter ? treeFileId : fileId) || undefined,
        node_id: useTreeFilter && nodeId ? nodeId : undefined,
        page,
        page_size: PAGE_SIZE,
      }
      const data = await getKnowledgeChunks(taskId, params)
      setItems(Array.isArray(data?.items) ? data.items : [])
      setTotal(Number(data?.total) || 0)
      setSearchDegraded(Boolean(data?.search_degraded))
      setError('')
    } catch (err) {
      setItems([])
      setTotal(0)
      setSearchDegraded(false)
      setError(err.message || '加载知识块失败')
    } finally {
      setLoading(false)
    }
  }, [
    taskId,
    q,
    segmentLevel,
    tag,
    source,
    indexStatus,
    fileId,
    useTreeFilter,
    treeFileId,
    nodeId,
    page,
  ])

  useEffect(() => {
    loadChunks()
  }, [loadChunks])

  const setChunkId = useCallback(
    (nextId) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (!next.get('tab')) next.set('tab', 'chunks')
        if (nextId) next.set('chunk_id', nextId)
        else next.delete('chunk_id')
        return next
      })
    },
    [setSearchParams],
  )

  const openChunk = useCallback(
    async (id) => {
      if (!taskId || !id) return
      setDetailLoading(true)
      setDetailError('')
      try {
        const data = await getKnowledgeChunk(taskId, id)
        setDetail(data)
      } catch (err) {
        setDetail(null)
        setDetailError(err.message || '加载知识块详情失败')
      } finally {
        setDetailLoading(false)
      }
    },
    [taskId],
  )

  useEffect(() => {
    if (!chunkId) {
      setDetail(null)
      setDetailError('')
      setDetailLoading(false)
      return
    }
    openChunk(chunkId)
  }, [chunkId, openChunk])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const canPrev = page > 1
  const canNext = page < totalPages

  function resetFilters() {
    setQInput('')
    setQ('')
    setSegmentLevel('')
    setTag('')
    setSource('')
    setIndexStatus('')
    setFileId('')
    setUseTreeFilter(false)
    setTreeFileId('')
    setNodeId('')
    setPage(1)
  }

  return (
    <div className="chunks-tab">
      <div className="chunks-toolbar">
        <div className="chunks-search-row">
          <label className="field chunks-search-field">
            搜索
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="标题 / 概要 / 描述（FTS）"
            />
          </label>
          {searchDegraded && (
            <span className="chunk-degraded-badge" title="全文检索不可用，已降级为字段匹配">
              搜索已降级（非 FTS）
            </span>
          )}
        </div>

        <div className="chunks-filters">
          <label className="field">
            粒度
            <select
              value={segmentLevel}
              onChange={(e) => {
                setSegmentLevel(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部</option>
              <option value="fine">fine</option>
              <option value="large">large</option>
            </select>
          </label>

          <label className="field">
            标签
            <select
              value={tag}
              onChange={(e) => {
                setTag(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部</option>
              {tags.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            来源
            <select
              value={source}
              onChange={(e) => {
                setSource(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部</option>
              <option value="native_text">native_text</option>
              <option value="ocr">ocr</option>
              <option value="table">table</option>
            </select>
          </label>

          <label className="field">
            索引状态
            <select
              value={indexStatus}
              onChange={(e) => {
                setIndexStatus(e.target.value)
                setPage(1)
              }}
            >
              <option value="">全部</option>
              <option value="pending">pending</option>
              <option value="ready">ready</option>
              <option value="failed">failed</option>
            </select>
          </label>

          {!useTreeFilter && (
            <label className="field">
              文件
              <select
                value={fileId}
                onChange={(e) => {
                  setFileId(e.target.value)
                  setPage(1)
                }}
              >
                <option value="">全部</option>
                {files.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.label || f.original_filename || f.id}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="chunks-tree-filter">
          <label className="chunks-checkbox">
            <input
              type="checkbox"
              checked={useTreeFilter}
              onChange={(e) => {
                const on = e.target.checked
                setUseTreeFilter(on)
                setPage(1)
                if (!on) {
                  setNodeId('')
                  setTreeFileId('')
                } else if (!treeFileId && files.length > 0) {
                  setTreeFileId(String(files[0].id))
                }
              }}
            />
            按章节树筛选
          </label>

          {useTreeFilter && (
            <div className="chunks-tree-panel">
              <label className="field">
                文件
                <select
                  value={treeFileId}
                  onChange={(e) => {
                    setTreeFileId(e.target.value)
                    setNodeId('')
                    setPage(1)
                  }}
                >
                  <option value="">选择文件</option>
                  {files.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.label || f.original_filename || f.id}
                    </option>
                  ))}
                </select>
              </label>
              {treeLoading ? (
                <p className="empty-state-hint">加载目录中…</p>
              ) : treeError ? (
                <p className="page-error">{treeError}</p>
              ) : treeFileId ? (
                <div className="chunks-tree-wrap">
                  <DocumentTree
                    nodes={treeNodes}
                    selectedId={nodeId || null}
                    onSelect={(node) => {
                      setNodeId(node.id)
                      setPage(1)
                    }}
                  />
                  {nodeId && (
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={() => {
                        setNodeId('')
                        setPage(1)
                      }}
                    >
                      清除节点筛选
                    </button>
                  )}
                </div>
              ) : (
                <p className="empty-state-hint">请选择文件以加载章节树</p>
              )}
            </div>
          )}
        </div>
      </div>

      {error && <p className="page-error">{error}</p>}

      <div className="chunks-table-meta">
        <span>
          共 <strong>{total}</strong> 条
          {total > 0 && (
            <>
              ，第 {page} / {totalPages} 页
            </>
          )}
        </span>
        <button type="button" className="btn btn-sm btn-secondary" onClick={resetFilters}>
          清空筛选
        </button>
      </div>

      <div className="admin-table-wrap">
        <table className="admin-table chunks-table">
          <thead>
            <tr>
              <th>粒度</th>
              <th>标题</th>
              <th>路径</th>
              <th>标签</th>
              <th>来源</th>
              <th>索引 / 向量</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6} className="empty-state-hint">
                  加载中…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-state-hint">
                  无匹配知识块，可清空筛选或查看「索引状态」Tab
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr
                  key={item.chunk_id}
                  className={`chunks-row${chunkId === item.chunk_id ? ' chunks-row-active' : ''}`}
                  onClick={() => setChunkId(item.chunk_id)}
                >
                  <td>
                    <span className={`chunk-level-badge chunk-level-${item.segment_level || ''}`}>
                      {item.segment_level || '—'}
                    </span>
                  </td>
                  <td title={item.title}>{item.title || '—'}</td>
                  <td title={(item.title_path || []).join(' / ')}>{pathTail(item.title_path)}</td>
                  <td title={formatTags(item.tags, 99)}>{formatTags(item.tags)}</td>
                  <td>{item.source || '—'}</td>
                  <td>
                    {item.index_status || '—'} / {item.embedding_status || '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="chunks-pagination">
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={!canPrev || loading}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          上一页
        </button>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={!canNext || loading}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </button>
      </div>

      {(chunkId || detailLoading || detailError) && (
        <ChunkDetailDrawer
          taskId={taskId}
          chunk={detail}
          loading={detailLoading}
          error={detailError}
          onClose={() => setChunkId('')}
          onOpenChunk={(id) => setChunkId(id)}
        />
      )}
    </div>
  )
}
