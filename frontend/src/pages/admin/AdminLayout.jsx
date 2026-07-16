import { NavLink, Outlet } from 'react-router-dom'

export default function AdminLayout() {
  return (
    <div className="admin-layout">
      <header className="admin-header">
        <h1>AdminLayout</h1>
        <nav className="admin-nav">
          <NavLink to="/admin/configs">Configs</NavLink>
          <NavLink to="/admin/tasks">Tasks</NavLink>
          <NavLink to="/">Back</NavLink>
        </nav>
      </header>
      <Outlet />
    </div>
  )
}
