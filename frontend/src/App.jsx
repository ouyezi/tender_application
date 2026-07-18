import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import TaskListPage from './pages/TaskListPage'
import TaskDetailPage from './pages/TaskDetailPage'
import WorkspaceListPage from './pages/WorkspaceListPage'
import WorkspaceDetailPage from './pages/WorkspaceDetailPage'
import KnowledgeDebugPage from './pages/KnowledgeDebugPage'
import AdminLayout from './pages/admin/AdminLayout'
import ConfigsPage from './pages/admin/ConfigsPage'
import AdminTasksPage from './pages/admin/AdminTasksPage'
import './App.css'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TaskListPage />} />
        <Route path="/tasks/:id" element={<TaskDetailPage />} />
        <Route path="/workspaces" element={<WorkspaceListPage />} />
        <Route path="/workspaces/:taskId" element={<WorkspaceDetailPage />} />
        <Route path="/workspaces/:taskId/knowledge" element={<KnowledgeDebugPage />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="configs" replace />} />
          <Route path="configs" element={<ConfigsPage />} />
          <Route path="tasks" element={<AdminTasksPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
