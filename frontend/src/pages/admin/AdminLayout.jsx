import { NavLink, Outlet } from 'react-router-dom'

export default function AdminLayout() {
  return (
    <div className="admin-layout">
      <aside className="admin-sidebar">
        <div className="admin-sidebar-brand">管理后台</div>
        <nav className="admin-sidebar-nav">
          <NavLink
            to="/admin/configs"
            className={({ isActive }) =>
              `admin-nav-link${isActive ? ' active' : ''}`
            }
          >
            诊断项目配置
          </NavLink>
          <NavLink
            to="/admin/tasks"
            className={({ isActive }) =>
              `admin-nav-link${isActive ? ' active' : ''}`
            }
          >
            诊断任务
          </NavLink>
          <NavLink to="/workspaces" className="admin-nav-link">
            工作区
          </NavLink>
          <NavLink to="/" className="admin-nav-link admin-nav-back">
            返回诊断页
          </NavLink>
        </nav>
      </aside>
      <div className="admin-main">
        <Outlet />
      </div>
    </div>
  )
}
