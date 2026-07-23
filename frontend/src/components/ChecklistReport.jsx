import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getChecklist, retryChecklist } from '../api'

const IMPORTANCE_LABELS = {
  high: '高',
  medium: '中',
  low: '低',
}

function parseApiDetail(message) {
  if (!message) return ''
  try {
    const parsed = JSON.parse(message)
    if (parsed && typeof parsed.detail === 'string') return parsed.detail
  } catch {
    // not JSON
  }
  return message
}

function formatJson(value) {
  if (value == null) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function isLegacyUnavailableError(detail) {
  return !detail || detail === 'Checklist not available'
}

export default function ChecklistReport({
  taskId,
  taskStatus,
  failureStage,
  errorMessage,
  onRetryStarted,
}) {
  const [report, setReport] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retrying, setRetrying] = useState(false)
  const [selectedCategory, setSelectedCategory] = useState('all')
  const [expandedItemId, setExpandedItemId] = useState(null)

  const load = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    setLoadError('')
    if (taskStatus === 'generating_checklist') {
      setReport(null)
      setLoading(false)
      return
    }
    try {
      const data = await getChecklist(taskId)
      setReport(data)
      setSelectedCategory('all')
      setExpandedItemId(null)
    } catch (err) {
      setReport(null)
      setLoadError(parseApiDetail(err.message))
    } finally {
      setLoading(false)
    }
  }, [taskId, taskStatus])

  useEffect(() => {
    load()
  }, [load])

  const flatItems = useMemo(() => {
    if (!report?.categories) return []
    return report.categories.flatMap((category) =>
      (category.items || []).map((item) => ({
        ...item,
        categoryName: category.name,
        categoryId: category.id,
      })),
    )
  }, [report])

  const filteredItems = useMemo(() => {
    if (selectedCategory === 'all') return flatItems
    return flatItems.filter((item) => item.categoryId === selectedCategory)
  }, [flatItems, selectedCategory])

  async function handleRetry() {
    setRetrying(true)
    try {
      await retryChecklist(taskId)
      onRetryStarted?.()
    } catch (err) {
      window.alert(`重试失败：${parseApiDetail(err.message) || '未知错误'}`)
    } finally {
      setRetrying(false)
    }
  }

  function toggleItemExpansion(itemId) {
    setExpandedItemId((current) => (current === itemId ? null : itemId))
  }

  if (loading) {
    return <p className="report-pending">加载检查项报告…</p>
  }

  if (taskStatus === 'generating_checklist') {
    return <p className="report-pending">检查项生成中…</p>
  }

  if (report) {
    const { summary, generation, categories } = report
    const importanceCounts = summary?.importance_counts || {}

    return (
      <div className="checklist-report">
        <div className="checklist-summary">
          <span className="checklist-summary-item">
            状态：<strong>{generation?.status || '—'}</strong>
          </span>
          <span className="checklist-summary-item">
            分类：<strong>{summary?.category_count ?? 0}</strong>
          </span>
          <span className="checklist-summary-item">
            检查项：<strong>{summary?.item_count ?? 0}</strong>
          </span>
          <span className="checklist-summary-item">
            重要性：
            <strong>
              高 {importanceCounts.high ?? 0} / 中 {importanceCounts.medium ?? 0} / 低{' '}
              {importanceCounts.low ?? 0}
            </strong>
          </span>
        </div>

        <div className="checklist-filters" role="group" aria-label="分类筛选">
          <button
            type="button"
            className={
              selectedCategory === 'all'
                ? 'checklist-filter-chip active'
                : 'checklist-filter-chip'
            }
            onClick={() => setSelectedCategory('all')}
          >
            全部
          </button>
          {(categories || []).map((category) => (
            <button
              key={category.id}
              type="button"
              className={
                selectedCategory === category.id
                  ? 'checklist-filter-chip active'
                  : 'checklist-filter-chip'
              }
              onClick={() => setSelectedCategory(category.id)}
            >
              {category.name}
            </button>
          ))}
        </div>

        {filteredItems.length === 0 ? (
          <p className="empty-state-hint">当前筛选下暂无检查项</p>
        ) : (
          <div className="checklist-table-wrap">
            <table className="checklist-table">
              <thead>
                <tr>
                  <th scope="col" className="checklist-expand-head">
                    详情
                  </th>
                  <th>诊断标题</th>
                  <th>诊断要求</th>
                  <th>诊断技巧</th>
                  <th>重要性</th>
                  <th>分类</th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.map((item) => {
                  const expanded = expandedItemId === item.id
                  const itemTitle = item.title || '未命名检查项'
                  return (
                    <Fragment key={item.id}>
                      <tr className={expanded ? 'checklist-row-expanded' : 'checklist-row'}>
                        <td className="checklist-expand-cell">
                          <button
                            type="button"
                            className="checklist-toggle"
                            onClick={() => toggleItemExpansion(item.id)}
                            aria-label={expanded ? `收起 ${itemTitle}` : `展开 ${itemTitle}`}
                            aria-expanded={expanded}
                          >
                            {expanded ? '▾' : '▸'}
                          </button>
                        </td>
                        <td>
                          {item.title || '—'}
                          {item.diagnosis_mode === 'offline' && (
                            <span className="checklist-offline-tag">线下核验</span>
                          )}
                        </td>
                        <td>{item.requirement || '—'}</td>
                        <td>{item.technique || '—'}</td>
                        <td>{IMPORTANCE_LABELS[item.importance] || item.importance || '—'}</td>
                        <td>{item.categoryName || '—'}</td>
                      </tr>
                      {expanded && (
                        <tr className="checklist-expand-row">
                          <td colSpan={6}>
                            <div className="checklist-expand">
                              <div className="checklist-expand-block">
                                <span className="checklist-expand-label">来源引用</span>
                                <pre className="checklist-md">{item.source_citations || '—'}</pre>
                              </div>
                              <div className="checklist-expand-block">
                                <span className="checklist-expand-label">检索提示</span>
                                <pre>{formatJson(item.retrieval_hints)}</pre>
                              </div>
                              <div className="checklist-expand-block">
                                <span className="checklist-expand-label">预期证据</span>
                                <pre className="checklist-md">{item.expected_evidence || '—'}</pre>
                              </div>
                              <div className="checklist-expand-block">
                                <span className="checklist-expand-label">符合性规则</span>
                                <pre className="checklist-md">{item.compliance_rules || '—'}</pre>
                              </div>
                              <div className="checklist-expand-block">
                                <span className="checklist-expand-label">后果规则</span>
                                <pre className="checklist-md">{item.consequence_rules || '—'}</pre>
                              </div>
                              <div className="checklist-expand-block">
                                <span className="checklist-expand-label">管理配置引用</span>
                                <pre>{formatJson(item.admin_config_refs)}</pre>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  if (taskStatus === 'failed' && failureStage === 'tender_parse') {
    return (
      <div className="checklist-empty">
        <p className="page-error">
          {errorMessage || '招标文件解析失败，无法生成检查项。'}
        </p>
        <p className="empty-state-hint">
          请前往工作区重新解析招标文件后再试。
        </p>
        <Link className="btn btn-secondary" to={`/workspaces/${taskId}`}>
          打开工作区
        </Link>
      </div>
    )
  }

  if (
    taskStatus === 'failed' &&
    (failureStage === 'checklist_generation' ||
      failureStage === 'checklist_validation' ||
      loadError === 'checklist_data_invalid')
  ) {
    return (
      <div className="checklist-empty">
        <p className="page-error">
          {errorMessage || loadError || '检查项生成失败'}
        </p>
        <button
          type="button"
          className="btn btn-primary"
          disabled={retrying}
          onClick={handleRetry}
        >
          {retrying ? '重试中…' : '重试生成'}
        </button>
      </div>
    )
  }

  if (loadError === 'checklist_data_invalid') {
    return (
      <div className="checklist-empty">
        <p className="page-error">检查项数据异常，请联系管理员。</p>
      </div>
    )
  }

  if (!isLegacyUnavailableError(loadError)) {
    return <p className="page-error">{loadError}</p>
  }

  return <p className="empty-state-hint">暂无检查项报告</p>
}
