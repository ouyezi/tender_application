import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { deleteTask, listTasks } from '../api'
import TaskCard from '../components/TaskCard'
import CreateTaskModal from '../components/CreateTaskModal'

export default function TaskListPage() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [deletingId, setDeletingId] = useState(null)

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const data = await listTasks()
      setTasks(Array.isArray(data) ? data : [])
      setError('')
    } catch (err) {
      setError(err.message || '加载任务列表失败')
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(() => refresh(true), 3000)
    return () => clearInterval(timer)
  }, [refresh])

  function handleCreated(task) {
    setModalOpen(false)
    refresh(true)
    if (task?.id) {
      navigate(`/tasks/${task.id}`)
    }
  }

  async function handleDelete(task) {
    if (!window.confirm('确定删除该诊断任务？此操作不可恢复。')) return

    setDeletingId(task.id)
    setError('')
    try {
      await deleteTask(task.id)
      await refresh(true)
    } catch (err) {
      setError(err.message || '删除任务失败')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <main className="page task-list-page">
      <header className="page-header">
        <div className="page-header-titles">
          <h1>标书诊断</h1>
          <Link className="header-link" to="/workspaces">
            工作区
          </Link>
          <Link className="header-link" to="/admin">
            管理后台
          </Link>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setModalOpen(true)}
        >
          创建诊断
        </button>
      </header>

      {error && <p className="page-error">{error}</p>}

      {loading && tasks.length === 0 ? (
        <p className="empty-state">加载中…</p>
      ) : tasks.length === 0 ? (
        <div className="empty-state">
          <p>暂无诊断任务</p>
          <p className="empty-state-hint">点击「创建诊断」上传招标与投标文件开始分析</p>
        </div>
      ) : (
        <div className="task-grid">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onClick={(t) => navigate(`/tasks/${t.id}`)}
              onDelete={handleDelete}
              deleting={deletingId === task.id}
            />
          ))}
        </div>
      )}

      <CreateTaskModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={handleCreated}
      />
    </main>
  )
}
