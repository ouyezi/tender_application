const COMPLIANCE_LABELS = {
  satisfied: '满足',
  violated: '违反',
  cannot_satisfy: '不能满足',
  insufficient_evidence: '证据不足',
  通过: '通过',
  风险: '风险',
  缺失: '缺失',
}

const CONSEQUENCE_LABELS = {
  no_score: '不得分',
  bid_unusable: '投标无效',
  score_risk: '得分风险',
  general_risk: '一般风险',
}

const COLUMNS = [
  { key: 'content_title', label: '诊断内容' },
  { key: 'description', label: '诊断描述' },
  { key: 'result', label: '结果' },
  { key: 'consequence', label: '后果' },
  { key: 'evidence', label: '证据' },
  { key: 'suggestion', label: '建议' },
]

function formatCompliance(row) {
  const raw = row.compliance_status || row.result || ''
  if (!raw) return '—'
  return COMPLIANCE_LABELS[raw] || raw
}

function parseConsequenceTags(value) {
  if (Array.isArray(value)) return value
  if (typeof value !== 'string' || !value.trim()) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function formatConsequences(row) {
  const tags = parseConsequenceTags(row.consequence_tags)
  if (tags.length === 0) return '—'
  return tags.map((tag) => CONSEQUENCE_LABELS[tag] || tag).join('、')
}

function cellValue(row, key) {
  if (key === 'result') return formatCompliance(row)
  if (key === 'consequence') return formatConsequences(row)
  return row[key] || '—'
}

export default function ResultTable({ results }) {
  const rows = Array.isArray(results) ? results : []

  if (rows.length === 0) {
    return <p className="empty-state-hint">暂无诊断结果</p>
  }

  return (
    <div className="result-table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {COLUMNS.map((col) => (
              <th key={col.key}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id ?? `${row.config_id}-${row.sort_order}`}>
              {COLUMNS.map((col) => (
                <td key={col.key}>{cellValue(row, col.key)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
