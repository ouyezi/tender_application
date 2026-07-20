import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getExecutionGraph, getTask } from '../api'
import ExecutionGraph from '../components/execution/ExecutionGraph'

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'stopped'])

const TASK_STATUS_LABELS = {
  interpreting: '解读中',
  generating_checklist: '生成检查项',
  diagnosing: '诊断中',
  running: '诊断中',
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

const NODE_STATUS_LABELS = {
  pending: '等待',
  running: '运行中',
  completed: '完成',
  failed: '失败',
  interrupted: '中断',
  skipped: '跳过',
}

function formatDuration(ms) {
  if (ms == null || ms <= 0) return '—'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rem = seconds % 60
  return rem ? `${minutes}m ${rem}s` : `${minutes}m`
}

export default function TaskProcessPage() {
  const { id } = useParams()
  const [graph, setGraph] = useState(null)
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedKey, setSelectedKey] = useState(null)

  const load = useCallback(
    async (silent = false) => {
      if (!id) return
      if (!silent) setLoading(true)
      try {
        const [graphData, taskData] = await Promise.all([
          getExecutionGraph(id),
          getTask(id).catch(() => null),
        ])
        setGraph(graphData)
        setTask(taskData)
        setError('')
      } catch (err) {
        setError(err.message || '加载进程数据失败')
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [id],
  )

  useEffect(() => {
    load()
  }, [load])

  const taskStatus = graph?.task_status || task?.status || ''
  const isTerminal =
    graph?.is_terminal ?? (taskStatus ? TERMINAL_STATUSES.has(taskStatus) : false)

  useEffect(() => {
    if (!graph || isTerminal) return undefined
    const timer = setInterval(() => load(true), 2000)
    return () => clearInterval(timer)
  }, [graph, isTerminal, load])

  const selectedNode = useMemo(
    () => graph?.nodes?.find((n) => n.key === selectedKey) ?? null,
    [graph, selectedKey],
  )

  const runningNode = useMemo(
    () => graph?.nodes?.find((n) => n.status === 'running') ?? null,
    [graph],
  )

  if (loading && !graph) {
    return (
      <main className="page task-process-page">
        <p className="empty-state">加载中…</p>
      </main>
    )
  }

  if (error && !graph) {
    return (
      <main className="page task-process-page">
        <Link className="back-link" to={`/tasks/${id}`}>
          ← 返回任务详情
        </Link>
        <p className="page-error">{error}</p>
      </main>
    )
  }

  const statusLabel = TASK_STATUS_LABELS[taskStatus] || taskStatus || '—'
  const isLegacy = graph?.legacy || !graph?.nodes?.length
  const summary = graph?.summary

  return (
    <main className="page task-process-page">
      <header className="page-header">
        <div className="page-header-titles">
          <Link className="back-link" to={`/tasks/${id}`}>
            ← 返回任务详情
          </Link>
          <h1>执行进程</h1>
          <span className="task-process-id">{id}</span>
          {taskStatus && (
            <span className={`status-badge status-${taskStatus}`}>{statusLabel}</span>
          )}
        </div>
      </header>

      {error && <p className="page-error">{error}</p>}

      {isLegacy ? (
        <p className="empty-state process-legacy-message">
          暂无进程数据（该任务创建于进程图功能上线前）
        </p>
      ) : (
        <>
          {summary && (
            <section className="process-summary-bar">
              <div className="process-summary-item">
                <span className="process-summary-label">总耗时</span>
                <span className="process-summary-value">
                  {formatDuration(summary.total_duration_ms)}
                </span>
              </div>
              <div className="process-summary-item">
                <span className="process-summary-label">已完成</span>
                <span className="process-summary-value">
                  {summary.completed}/{summary.total_nodes}
                </span>
              </div>
              <div className="process-summary-item">
                <span className="process-summary-label">当前节点</span>
                <span className="process-summary-value">
                  {runningNode ? runningNode.label : '—'}
                </span>
              </div>
            </section>
          )}

          <div className="process-layout">
            <section className="process-graph-panel">
              <ExecutionGraph
                graph={graph}
                selectedKey={selectedKey}
                onSelectNode={setSelectedKey}
              />
            </section>

            <aside className="process-detail-panel">
              <h2>节点详情</h2>
              {selectedNode ? (
                <div className="process-node-detail">
                  <dl className="process-detail-list">
                    <div>
                      <dt>名称</dt>
                      <dd>{selectedNode.label}</dd>
                    </div>
                    <div>
                      <dt>状态</dt>
                      <dd>
                        <span className={`execution-node-status node-status-${selectedNode.status}`}>
                          {NODE_STATUS_LABELS[selectedNode.status] || selectedNode.status}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt>耗时</dt>
                      <dd>{formatDuration(selectedNode.duration_ms)}</dd>
                    </div>
                    <div>
                      <dt>Key</dt>
                      <dd>
                        <code>{selectedNode.key}</code>
                      </dd>
                    </div>
                  </dl>
                  {selectedNode.meta && Object.keys(selectedNode.meta).length > 0 && (
                    <div className="process-meta-block">
                      <h3>Meta</h3>
                      <pre className="process-meta-json">
                        {JSON.stringify(selectedNode.meta, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              ) : (
                <p className="empty-state-hint">点击图中节点查看详情</p>
              )}
            </aside>
          </div>
        </>
      )}
    </main>
  )
}
