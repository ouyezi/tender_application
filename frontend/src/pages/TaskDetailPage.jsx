import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fileUrl, getTask, interpretHtmlUrl, reportDocxUrl } from '../api'
import MarkdownPreview from '../components/MarkdownPreview'
import ResultTable from '../components/ResultTable'

const STATUS_LABELS = {
  interpreting: '解读中',
  diagnosing: '诊断中',
  running: '诊断中', // legacy
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

const POLL_STATUSES = new Set(['interpreting', 'diagnosing', 'running', 'paused'])

export default function TaskDetailPage() {
  const { id } = useParams()
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reportTab, setReportTab] = useState('interpret')

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
  const results = task.results || []
  const canDownloadInterpret = Boolean(task.interpret_html_path || task.interpret_markdown)

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
        <div className="page-header-actions">
          <Link className="btn btn-secondary" to={`/workspaces/${task.id}`}>
            打开工作区
          </Link>
          {canDownloadInterpret && (
            <a className="btn btn-secondary" href={interpretHtmlUrl(task.id)}>
              下载解读报告
            </a>
          )}
          {status === 'completed' && (
            <a className="btn btn-primary" href={reportDocxUrl(task.id)}>
              下载诊断报告
            </a>
          )}
        </div>
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
        <div className="report-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={reportTab === 'interpret'}
            className={reportTab === 'interpret' ? 'report-tab active' : 'report-tab'}
            onClick={() => setReportTab('interpret')}
          >
            解读报告
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={reportTab === 'diagnosis'}
            className={reportTab === 'diagnosis' ? 'report-tab active' : 'report-tab'}
            onClick={() => setReportTab('diagnosis')}
          >
            诊断报告
          </button>
        </div>

        {reportTab === 'interpret' ? (
          status === 'interpreting' && !task.interpret_markdown ? (
            <p className="report-pending">招标文件解读中…</p>
          ) : task.interpret_markdown ? (
            <MarkdownPreview markdown={task.interpret_markdown} />
          ) : status === 'failed' ? (
            <p className="page-error">{task.error_message || '解读失败'}</p>
          ) : status === 'stopped' ? (
            <p className="empty-state-hint">已停止，暂无报告</p>
          ) : (
            <p className="empty-state-hint">暂无解读报告</p>
          )
        ) : status === 'interpreting' ? (
          <p className="report-pending">解读完成后开始诊断</p>
        ) : (status === 'diagnosing' || status === 'running' || status === 'paused') &&
          !task.report_markdown ? (
          <p className="report-pending">诊断进行中…</p>
        ) : task.report_markdown ? (
          <MarkdownPreview markdown={task.report_markdown} />
        ) : status === 'failed' && !task.interpret_markdown ? (
          <p className="empty-state-hint">未开始诊断</p>
        ) : status === 'failed' ? (
          <p className="page-error">{task.error_message || '诊断失败'}</p>
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
