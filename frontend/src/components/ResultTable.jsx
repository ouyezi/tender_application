const COLUMNS = [
  { key: 'content_title', label: '诊断内容' },
  { key: 'description', label: '诊断描述' },
  { key: 'result', label: '结果' },
  { key: 'evidence', label: '证据' },
  { key: 'suggestion', label: '建议' },
]

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
                <td key={col.key}>{row[col.key] || '—'}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
