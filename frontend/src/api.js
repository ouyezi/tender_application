async function request(path, options = {}) {
  const res = await fetch(path, options)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `${res.status} ${res.statusText}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export function listTasks() {
  return request('/api/tasks')
}

export function getTask(id) {
  return request(`/api/tasks/${id}`)
}

export function createTask(formData) {
  return request('/api/tasks', { method: 'POST', body: formData })
}

export function listConfigs() {
  return request('/api/configs')
}

export function createConfig(data) {
  return request('/api/configs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function updateConfig(id, data) {
  return request(`/api/configs/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function deleteConfig(id) {
  return request(`/api/configs/${id}`, { method: 'DELETE' })
}

export function pauseTask(id) {
  return request(`/api/tasks/${id}/pause`, { method: 'POST' })
}

export function resumeTask(id) {
  return request(`/api/tasks/${id}/resume`, { method: 'POST' })
}

export function stopTask(id) {
  return request(`/api/tasks/${id}/stop`, { method: 'POST' })
}

export function reportDocxUrl(id) {
  return `/api/tasks/${id}/report.docx`
}

export function fileUrl(id, kind) {
  return `/api/tasks/${id}/files/${kind}`
}
