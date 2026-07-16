import { Link, useParams } from 'react-router-dom'

export default function WorkspaceDetailPage() {
  const { taskId } = useParams()

  return (
    <main className="page workspace-detail-page">
      <header className="page-header">
        <div className="page-header-titles">
          <Link className="back-link" to="/workspaces">
            ← 返回工作区列表
          </Link>
          <h1>工作区详情</h1>
        </div>
      </header>

      <div className="detail-section">
        <p className="empty-state">加载中…</p>
        <p className="empty-state-hint">
          任务 <code>{taskId}</code> 的文档阅读器将在 Task 10 实现。
        </p>
      </div>
    </main>
  )
}
