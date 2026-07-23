import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  fileUrl,
  generateChecklist,
  generateInterpretHtml,
  getTask,
  indexBid,
  interpretHtmlUrl,
  pauseTask,
  reportDocxUrl,
  resumeTask,
  runDiagnosis,
  runFullDiagnosis,
} from '../api'
import ChecklistReport from '../components/ChecklistReport'
import MarkdownPreview from '../components/MarkdownPreview'
import ResultTable from '../components/ResultTable'

const STATUS_LABELS = {
  draft: '待执行',
  interpreting: '解读中',
  generating_checklist: '生成检查项',
  indexing_bid: '标书索引中',
  diagnosing: '诊断中',
  running: '诊断中', // legacy
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

const POLL_STATUSES = new Set([
  'draft',
  'interpreting',
  'generating_checklist',
  'indexing_bid',
  'diagnosing',
  'running',
  'paused',
])

export default function TaskDetailPage() {
  const { id } = useParams()
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [actionLoading, setActionLoading] = useState('')
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
    if (!task) return undefined
    const readiness = task.readiness || {}
    const laneActive =
      readiness.checklist_lane_active ||
      readiness.bid_index_lane_active ||
      readiness.diagnosis_lane_active ||
      readiness.full_run_active ||
      readiness.interpret_html_lane_active
    const shouldPoll =
      readiness.interpret_html_lane_active ||
      (POLL_STATUSES.has(task.status) && (task.status !== 'draft' || laneActive))
    if (!shouldPoll) return undefined
    const timer = setInterval(() => load(true), 2000)
    return () => clearInterval(timer)
  }, [task?.status, task?.readiness, load])

  async function runAction(key, fn) {
    setActionError('')
    setActionLoading(key)
    try {
      await fn(id)
      await load(true)
    } catch (err) {
      setActionError(err.message || '操作失败')
    } finally {
      setActionLoading('')
    }
  }

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
  const readiness = task.readiness || {}
  const terminal = new Set(['completed', 'stopped', 'failed'])
  const isTerminal = terminal.has(status)
  const isPaused = status === 'paused'
  const isRunning = !isTerminal && !isPaused && status !== 'draft'

  const canGenerateChecklist =
    !isTerminal &&
    !readiness.checklist_ready &&
    !readiness.checklist_lane_active &&
    !readiness.full_run_active

  const canIndexBid =
    !isTerminal &&
    !readiness.bid_index_ready &&
    !readiness.bid_index_lane_active &&
    !readiness.full_run_active

  const canDiagnose =
    !isTerminal &&
    readiness.diagnosis_ready &&
    status !== 'completed' &&
    !readiness.diagnosis_lane_active &&
    !readiness.full_run_active

  const canRunFull = !isTerminal && !readiness.full_run_active

  const canPause = isRunning
  const canResume = isPaused

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
          <Link className="process-link" to={`/tasks/${id}/process`}>
            查看进程 →
          </Link>
          <Link className="btn btn-secondary" to={`/workspaces/${task.id}`}>
            打开工作区
          </Link>
          {task.interpret_markdown && (() => {
            const htmlReady = readiness.interpret_html_ready
            const htmlGenerating = readiness.interpret_html_lane_active
            const htmlError = readiness.interpret_html_error

            if (htmlGenerating) {
              return (
                <button type="button" className="btn btn-secondary" disabled>
                  HTML 生成中…
                </button>
              )
            }
            if (htmlReady) {
              return (
                <>
                  <a className="btn btn-primary" href={interpretHtmlUrl(task.id)} download>
                    直接下载
                  </a>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={Boolean(actionLoading)}
                    onClick={() => {
                      if (!window.confirm('将覆盖已生成的 HTML 报告，是否继续？')) return
                      runAction('interpret-html', () => generateInterpretHtml(id))
                    }}
                  >
                    {actionLoading === 'interpret-html' ? '提交中…' : '重新生成'}
                  </button>
                </>
              )
            }
            return (
              <>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={Boolean(actionLoading)}
                  onClick={() => runAction('interpret-html', () => generateInterpretHtml(id))}
                >
                  {actionLoading === 'interpret-html' ? '提交中…' : '下载解读报告'}
                </button>
                {htmlError && <span className="page-error">{htmlError}</span>}
              </>
            )
          })()}
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

        <div className="detail-step-bar">
          <div className={`detail-step${readiness.checklist_ready ? ' is-done' : ''}`}>
            <span className="detail-step-dot" />
            <span className="detail-step-title">诊断项</span>
            <span className="detail-step-status">
              {readiness.checklist_lane_active
                ? '生成中'
                : readiness.checklist_ready
                  ? '已生成'
                  : '待执行'}
            </span>
          </div>
          <div className={`detail-step${readiness.bid_index_ready ? ' is-done' : ''}`}>
            <span className="detail-step-dot" />
            <span className="detail-step-title">标书索引</span>
            <span className="detail-step-status">
              {readiness.bid_index_lane_active || status === 'indexing_bid'
                ? '索引中'
                : readiness.bid_index_ready
                  ? '已就绪'
                  : readiness.bid_index_required
                    ? '待执行'
                    : '无需索引'}
            </span>
          </div>
          <div className={`detail-step${status === 'completed' ? ' is-done' : ''}`}>
            <span className="detail-step-dot" />
            <span className="detail-step-title">诊断</span>
            <span className="detail-step-status">
              {status === 'completed'
                ? '已完成'
                : readiness.diagnosis_lane_active || status === 'diagnosing'
                  ? '诊断中'
                  : '待执行'}
            </span>
          </div>
        </div>

        <div className="detail-actions">
          <div className="detail-actions-group">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canGenerateChecklist || Boolean(actionLoading)}
              onClick={() => runAction('checklist', () => generateChecklist(id))}
            >
              {actionLoading === 'checklist' ? '生成中…' : '生成诊断项'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canIndexBid || Boolean(actionLoading)}
              onClick={() => runAction('index', () => indexBid(id))}
            >
              {actionLoading === 'index' ? '索引中…' : '标书索引'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canDiagnose || Boolean(actionLoading)}
              onClick={() => runAction('diagnose', () => runDiagnosis(id))}
            >
              {actionLoading === 'diagnose' ? '诊断中…' : '诊断'}
            </button>
          </div>
          <div className="detail-actions-group">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canRunFull || Boolean(actionLoading)}
              onClick={() => runAction('full', () => runFullDiagnosis(id))}
            >
              {actionLoading === 'full' ? '执行中…' : '一键诊断'}
            </button>
            {canPause && (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={Boolean(actionLoading)}
                onClick={() => runAction('pause', () => pauseTask(id))}
              >
                暂停
              </button>
            )}
            {canResume && (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={Boolean(actionLoading)}
                onClick={() => runAction('resume', () => resumeTask(id))}
              >
                继续
              </button>
            )}
          </div>
        </div>
        {actionError && <p className="page-error">{actionError}</p>}

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
            aria-selected={reportTab === 'checklist'}
            className={reportTab === 'checklist' ? 'report-tab active' : 'report-tab'}
            onClick={() => setReportTab('checklist')}
          >
            检查项报告
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
        ) : reportTab === 'checklist' ? (
          <ChecklistReport
            taskId={task.id}
            taskStatus={status}
            failureStage={task.failure_stage}
            errorMessage={task.error_message}
            onRetryStarted={() => load(true)}
          />
        ) : status === 'interpreting' ? (
          <p className="report-pending">解读完成后开始生成检查项</p>
        ) : status === 'generating_checklist' ? (
          <p className="report-pending">检查项生成中…</p>
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
