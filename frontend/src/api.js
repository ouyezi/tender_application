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

export function deleteTask(id) {
  return request(`/api/tasks/${id}`, { method: 'DELETE' })
}

export function reportDocxUrl(id) {
  return `/api/tasks/${id}/report.docx`
}

export function interpretHtmlUrl(id) {
  return `/api/tasks/${id}/interpret.html`
}

export function fileUrl(id, kind) {
  return `/api/tasks/${id}/files/${kind}`
}

export function listWorkspaces() {
  return request('/api/workspaces')
}

export function getWorkspace(taskId) {
  return request(`/api/workspaces/${taskId}`)
}

export function importWorkspaceFile(taskId, formData) {
  return request(`/api/workspaces/${taskId}/files`, { method: 'POST', body: formData })
}

export function getWorkspaceTree(taskId, fileId) {
  return request(`/api/workspaces/${taskId}/files/${fileId}/tree`)
}

export function getWorkspaceContent(taskId, fileId, nodeId) {
  return request(
    `/api/workspaces/${taskId}/files/${fileId}/content?node_id=${encodeURIComponent(nodeId)}`,
  )
}

export function reparseWorkspaceFile(taskId, fileId) {
  return request(`/api/workspaces/${taskId}/files/${fileId}/reparse`, { method: 'POST' })
}

export function workspaceFileDownloadUrl(taskId, fileId) {
  return `/api/workspaces/${taskId}/files/${fileId}/download`
}

export function getChecklist(taskId) {
  return request(`/api/tasks/${taskId}/checklist`)
}

export function retryChecklist(taskId) {
  return request(`/api/tasks/${taskId}/checklist/retry`, { method: 'POST' })
}

export function getKnowledgeIndexStatus(taskId) {
  return request(`/api/workspaces/${taskId}/knowledge/index-status`)
}

export function getKnowledgeChunks(taskId, params = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const q = qs.toString()
  return request(`/api/workspaces/${taskId}/knowledge/chunks${q ? `?${q}` : ''}`)
}

export function getKnowledgeChunk(taskId, chunkId) {
  return request(`/api/workspaces/${taskId}/knowledge/chunks/${encodeURIComponent(chunkId)}`)
}

export function getKnowledgeTags(taskId) {
  return request(`/api/workspaces/${taskId}/knowledge/tags`)
}

export function getKnowledgeWiki(taskId) {
  return request(`/api/workspaces/${taskId}/knowledge/wiki`)
}

export function getKnowledgeWikiPage(taskId, wikiId) {
  return request(`/api/workspaces/${taskId}/knowledge/wiki/${wikiId}`)
}

export function debugKnowledgeRetrieve(taskId, body) {
  return request(`/api/workspaces/${taskId}/knowledge/debug/retrieve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
