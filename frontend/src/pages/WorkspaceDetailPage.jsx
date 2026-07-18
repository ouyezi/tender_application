import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  getWorkspace,
  getWorkspaceContent,
  getWorkspaceTree,
  reparseWorkspaceFile,
  workspaceFileDownloadUrl,
} from '../api'
import DocumentTree from '../components/DocumentTree'
import ImportFileModal from '../components/ImportFileModal'
import MarkdownPreview from '../components/MarkdownPreview'

const PARSE_STATUS_LABELS = {
  pending: '待解析',
  running: '解析中',
  succeeded: '已解析',
  partial: '部分成功',
  failed: '解析失败',
  skipped: '不解析',
}

const OPENABLE_STATUSES = new Set(['succeeded', 'partial'])
const REPARSEABLE_STATUSES = new Set(['failed', 'partial'])
const PENDING_STATUSES = new Set(['pending', 'running'])

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

function extractNodes(data) {
  if (Array.isArray(data)) return data
  if (Array.isArray(data?.nodes)) return data.nodes
  return []
}

export default function WorkspaceDetailPage() {
  const { taskId } = useParams()

  const [workspace, setWorkspace] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reparsingIds, setReparsingIds] = useState(() => new Set())
  const [importOpen, setImportOpen] = useState(false)

  const [selectedFile, setSelectedFile] = useState(null)
  const [treeNodes, setTreeNodes] = useState([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeError, setTreeError] = useState('')

  const [selectedNode, setSelectedNode] = useState(null)
  const [contentMarkdown, setContentMarkdown] = useState('')
  const [contentMeta, setContentMeta] = useState(null)
  const [contentLoading, setContentLoading] = useState(false)
  const [contentError, setContentError] = useState('')

  const load = useCallback(
    async (silent = false) => {
      if (!taskId) return
      if (!silent) setLoading(true)
      try {
        const data = await getWorkspace(taskId)
        setWorkspace(data)
        setError('')
      } catch (err) {
        setError(err.message || '加载工作区失败')
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [taskId],
  )

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const files = workspace?.files || []
    const hasPending = files.some((f) => PENDING_STATUSES.has(f.parse_status))
    if (!hasPending) return undefined
    const timer = setInterval(() => load(true), 2000)
    return () => clearInterval(timer)
  }, [workspace, load])

  const selectNode = useCallback(
    async (file, node) => {
      setSelectedNode(node)
      setContentError('')
      setContentLoading(true)
      setContentMeta(null)
      try {
        const data = await getWorkspaceContent(taskId, file.id, node.id)
        setContentMarkdown(data?.markdown || '')
        setContentMeta({
          title: data?.title || node.title || '',
          start_offset: data?.start_offset ?? node.self_start ?? 0,
          end_offset: data?.end_offset ?? node.subtree_end ?? 0,
          section_start: data?.section_start ?? node.start_offset ?? 0,
          section_end: data?.section_end ?? node.end_offset ?? 0,
        })
      } catch (err) {
        setContentError(err.message || '加载内容失败')
        setContentMarkdown('')
        setContentMeta(null)
      } finally {
        setContentLoading(false)
      }
    },
    [taskId],
  )

  const openFile = useCallback(
    async (file) => {
      if (!OPENABLE_STATUSES.has(file.parse_status)) return
      setSelectedFile(file)
      setSelectedNode(null)
      setContentMarkdown('')
      setContentMeta(null)
      setContentError('')
      setTreeNodes([])
      setTreeError('')
      setTreeLoading(true)
      try {
        const data = await getWorkspaceTree(taskId, file.id)
        const nodes = extractNodes(data)
        setTreeNodes(nodes)
        if (nodes.length > 0) {
          await selectNode(file, nodes[0])
        }
      } catch (err) {
        setTreeError(err.message || '加载目录失败')
      } finally {
        setTreeLoading(false)
      }
    },
    [taskId, selectNode],
  )

  async function handleReparse(file) {
    setReparsingIds((prev) => new Set(prev).add(file.id))
    try {
      await reparseWorkspaceFile(taskId, file.id)
      await load(true)
    } catch (err) {
      setError(err.message || '重新解析失败')
    } finally {
      setReparsingIds((prev) => {
        const next = new Set(prev)
        next.delete(file.id)
        return next
      })
    }
  }

  const files = workspace?.files || []

  return (
    <main className="page workspace-detail-page">
      <header className="page-header">
        <div className="page-header-titles">
          <Link className="back-link" to="/workspaces">
            ← 返回工作区列表
          </Link>
          <h1>工作区详情</h1>
        </div>
        <div className="page-header-actions">
          {taskId && (
            <>
              <Link className="btn btn-secondary" to={`/tasks/${taskId}`}>
                诊断详情
              </Link>
              <Link className="btn btn-secondary" to={`/workspaces/${taskId}/knowledge`}>
                知识检索
              </Link>
            </>
          )}
          <button type="button" className="btn btn-primary" onClick={() => setImportOpen(true)}>
            导入文件
          </button>
        </div>
      </header>

      {error && <p className="page-error">{error}</p>}

      {loading && !workspace ? (
        <p className="empty-state">加载中…</p>
      ) : !workspace ? (
        <p className="empty-state">工作区不存在</p>
      ) : (
        <>
          <section className="detail-section">
            <h2>文件</h2>
            {files.length === 0 ? (
              <p className="empty-state-hint">暂无文件，点击右上角「导入文件」添加</p>
            ) : (
              <div className="admin-table-wrap">
                <table className="admin-table workspace-file-table">
                  <thead>
                    <tr>
                      <th>标签</th>
                      <th>原始文件名</th>
                      <th>类型</th>
                      <th>解析状态</th>
                      <th>更新时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.map((file) => {
                      const openable = OPENABLE_STATUSES.has(file.parse_status)
                      const reparseable = REPARSEABLE_STATUSES.has(file.parse_status)
                      const isReparsing = reparsingIds.has(file.id)
                      const isActive = selectedFile?.id === file.id

                      return (
                        <tr
                          key={file.id}
                          className={[
                            'workspace-file-row',
                            openable ? 'workspace-file-row-clickable' : '',
                            isActive ? 'workspace-file-row-active' : '',
                          ]
                            .filter(Boolean)
                            .join(' ')}
                          onClick={openable ? () => openFile(file) : undefined}
                        >
                          <td>{file.label || '—'}</td>
                          <td title={file.original_filename}>{file.original_filename}</td>
                          <td>{file.ext || '—'}</td>
                          <td>
                            <span
                              className={`parse-status-badge parse-status-${file.parse_status}`}
                              title={file.parse_error || undefined}
                            >
                              {PARSE_STATUS_LABELS[file.parse_status] || file.parse_status}
                            </span>
                            {file.parse_status === 'partial' && (
                              <div className="parse-status-hint" title={file.parse_error || ''}>
                                {file.parse_error
                                  ? `部分表格抽取失败：${file.parse_error}`
                                  : '正文/目录已可用；部分表格抽取失败，可重试'}
                              </div>
                            )}
                            {file.parse_status === 'failed' && file.parse_error && (
                              <div className="parse-status-hint" title={file.parse_error}>
                                {file.parse_error}
                              </div>
                            )}
                          </td>
                          <td className="admin-task-time">{formatDate(file.updated_at)}</td>
                          <td className="admin-table-actions" onClick={(e) => e.stopPropagation()}>
                            <a
                              className="btn btn-sm btn-secondary"
                              href={workspaceFileDownloadUrl(taskId, file.id)}
                              download={file.original_filename}
                            >
                              下载
                            </a>
                            {reparseable && (
                              <button
                                type="button"
                                className="btn btn-sm btn-secondary"
                                onClick={() => handleReparse(file)}
                                disabled={isReparsing}
                              >
                                {isReparsing ? '重新解析中…' : '重新解析'}
                              </button>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="detail-section">
            <h2>文档阅读器</h2>
            {!selectedFile ? (
              <p className="empty-state-hint">点击上方已解析成功的文件，查看目录与内容</p>
            ) : (
              <div className="workspace-reader">
                <div className="workspace-reader-tree">
                  {treeLoading ? (
                    <p className="empty-state-hint">加载目录中…</p>
                  ) : treeError ? (
                    <p className="page-error">{treeError}</p>
                  ) : (
                    <DocumentTree
                      nodes={treeNodes}
                      selectedId={selectedNode?.id}
                      onSelect={(node) => selectNode(selectedFile, node)}
                    />
                  )}
                </div>
                <div className="workspace-reader-content">
                  {contentLoading ? (
                    <p className="empty-state-hint">加载内容中…</p>
                  ) : contentError ? (
                    <p className="page-error">{contentError}</p>
                  ) : contentMarkdown || contentMeta ? (
                    <>
                      {contentMeta && (
                        <div className="content-offset-meta">
                          <div className="content-offset-title">
                            {contentMeta.title || '当前章节'}
                          </div>
                          <div className="content-offset-ranges">
                            <span>
                              展示范围（含子章节）：
                              <code>
                                {contentMeta.start_offset} – {contentMeta.end_offset}
                              </code>
                              <span className="content-offset-len">
                                （{Math.max(0, contentMeta.end_offset - contentMeta.start_offset)} 字符）
                              </span>
                            </span>
                            <span>
                              本节正文：
                              <code>
                                {contentMeta.section_start} – {contentMeta.section_end}
                              </code>
                            </span>
                          </div>
                        </div>
                      )}
                      <MarkdownPreview markdown={contentMarkdown} />
                    </>
                  ) : (
                    <p className="empty-state-hint">选择左侧目录节点查看内容</p>
                  )}
                </div>
              </div>
            )}
          </section>
        </>
      )}

      <ImportFileModal
        taskId={taskId}
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onSuccess={() => load(true)}
      />
    </main>
  )
}
