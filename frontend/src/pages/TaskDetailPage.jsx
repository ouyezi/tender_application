import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fileUrl, getTask, reportDocxUrl } from '../api'
import MarkdownPreview from '../components/MarkdownPreview'
import ResultTable from '../components/ResultTable'

const STATUS_LABELS = {
  running: '诊断中',
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

const POLL_STATUSES = new Set(['running', 'paused'])

export default function TaskDetailPage() {
  const { id } = useParams()
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(
    async (silent = false) => {
      if (!id) return
      if (!silent) setLoading(true)
      try {
        const data = await getTask(id)
        setTask(data)
        setError('')
      } catch (err) {
        setError(err.message || '加载任务失败')
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [id],
  )

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!task || !POLL_STATUSES.has(task.status)) return undefined
    const timer = setInterval(() => load(true), 2000)
    return () => clearInterval(timer)
  }, [task?.status, load])

  if (loading && !task) {
    return (
      <main className="page task-detail-page">
        <p className="empty-state">加载中…</p>
      </main>
    )
  }

  if (error && !task) {
    return (
      <main className="page task-detail-page">
        <Link className="back-link" to="/">
          ← 返回列表
        </Link>
        <p className="page-error">{error}</p>
      </main>
    )
  }

  if (!task) {
    return (
      <main className="page task-detail-page">
        <Link className="back-link" to="/">
          ← 返回列表
        </Link>
        <p className="empty-state">任务不存在</p>
      </main>
    )
  }

  const status = task.status || 'running'
  const label = STATUS_LABELS[status] || status
  const isInProgress = POLL_STATUSES.has(status)
  const hasReport = Boolean(task.report_markdown)
  const results = task.results || []

  return (
    <main className="page task-detail-page">
      <header className="page-header">
        <div className="page-header-titles">
          <Link className="back-link" to="/">
            ← 返回列表
          </Link>
          <h1>任务详情</h1>
          <span className={`status-badge status-${status}`}>{label}</span>
        </div>
        {status === 'completed' && (
          <a className="btn btn-primary" href={reportDocxUrl(task.id)}>
            下载报告
          </a>
        )}
      </header>

      {error && <p className="page-error">{error}</p>}

      <section className="detail-section">
        <h2>文件</h2>
        <div className="detail-files">
          <div className="detail-file">
            <span className="task-card-label">招标文件</span>
            {task.tender_filename ? (
              <a href={fileUrl(task.id, 'tender')} download={task.tender_filename}>
                {task.tender_filename}
              </a>
            ) : (
              <span>—</span>
            )}
          </div>
          <div className="detail-file">
            <span className="task-card-label">投标文件</span>
            {task.bid_filename ? (
              <a href={fileUrl(task.id, 'bid')} download={task.bid_filename}>
                {task.bid_filename}
              </a>
            ) : (
              <span>—</span>
            )}
          </div>
        </div>
        <div className="detail-meta">
          <div className="detail-meta-block">
            <span className="task-card-label">项目背景</span>
            <p>{task.background || '—'}</p>
          </div>
          <div className="detail-meta-block">
            <span className="task-card-label">诊断要求</span>
            <p>{task.requirements || '—'}</p>
          </div>
        </div>
        {task.progress_total > 0 && (
          <p className="detail-progress">
            进度：{task.progress_done}/{task.progress_total}
          </p>
        )}
        {task.error_message && (
          <p className="page-error">{task.error_message}</p>
        )}
      </section>

      <section className="detail-section">
        <h2>报告预览</h2>
        {isInProgress && !hasReport ? (
          <p className="report-pending">诊断进行中，报告将在全部完成后生成</p>
        ) : hasReport ? (
          <MarkdownPreview markdown={task.report_markdown} />
        ) : (
          <p className="empty-state-hint">暂无报告</p>
        )}
      </section>

      <section className="detail-section">
        <h2>诊断结果</h2>
        <ResultTable results={results} />
      </section>
    </main>
  )
}
