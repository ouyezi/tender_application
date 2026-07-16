import { useCallback, useEffect, useState } from 'react'
import {
  listConfigs,
  createConfig,
  updateConfig,
  deleteConfig,
} from '../../api'

const EMPTY_FORM = {
  title: '',
  technique: '',
  content_mode: 'description',
  content_scope: '',
  content_text: '',
  importance: 'medium',
}

const IMPORTANCE_LABELS = {
  high: '高',
  medium: '中',
  low: '低',
}

const MODE_LABELS = {
  full_text: '全文',
  description: '内容描述',
}

function formatContent(config) {
  const mode = MODE_LABELS[config.content_mode] || config.content_mode
  if (config.content_mode === 'full_text') {
    const scope = config.content_scope || '—'
    return `${mode} · ${scope}`
  }
  const text = config.content_text || '—'
  return `${mode} · ${text}`
}

function toPayload(form) {
  return {
    title: form.title.trim(),
    technique: form.technique.trim(),
    content_mode: form.content_mode,
    content_scope:
      form.content_mode === 'full_text'
        ? form.content_scope.trim() || null
        : null,
    content_text:
      form.content_mode === 'description'
        ? form.content_text.trim() || null
        : null,
    importance: form.importance,
  }
}

export default function ConfigsPage() {
  const [configs, setConfigs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const data = await listConfigs()
      setConfigs(Array.isArray(data) ? data : [])
      setError('')
    } catch (err) {
      setError(err.message || '加载配置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  function openCreate() {
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormError('')
    setModalOpen(true)
  }

  function openEdit(config) {
    setEditing(config)
    setForm({
      title: config.title || '',
      technique: config.technique || '',
      content_mode: config.content_mode || 'description',
      content_scope: config.content_scope || '',
      content_text: config.content_text || '',
      importance: config.importance || 'medium',
    })
    setFormError('')
    setModalOpen(true)
  }

  function closeModal() {
    if (submitting) return
    setModalOpen(false)
    setEditing(null)
    setForm(EMPTY_FORM)
    setFormError('')
  }

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setFormError('')

    if (!form.title.trim()) {
      setFormError('请填写诊断标题')
      return
    }

    const payload = toPayload(form)
    setSubmitting(true)
    try {
      if (editing) {
        await updateConfig(editing.id, payload)
      } else {
        await createConfig(payload)
      }
      setModalOpen(false)
      setEditing(null)
      setForm(EMPTY_FORM)
      await refresh()
    } catch (err) {
      setFormError(err.message || '保存失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete(config) {
    const ok = window.confirm(`确定删除「${config.title}」？`)
    if (!ok) return
    try {
      await deleteConfig(config.id)
      await refresh()
    } catch (err) {
      setError(err.message || '删除失败')
    }
  }

  return (
    <main className="page admin-page">
      <header className="page-header">
        <h1>诊断项目配置</h1>
        <button type="button" className="btn btn-primary" onClick={openCreate}>
          新增
        </button>
      </header>

      {error && <p className="page-error">{error}</p>}

      {loading ? (
        <p className="empty-state">加载中…</p>
      ) : configs.length === 0 ? (
        <div className="empty-state">
          <p>暂无诊断配置</p>
          <p className="empty-state-hint">点击「新增」添加诊断项目</p>
        </div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>诊断标题</th>
                <th>诊断技巧</th>
                <th>诊断内容</th>
                <th>重要性</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {configs.map((config) => (
                <tr key={config.id}>
                  <td>{config.title}</td>
                  <td>{config.technique || '—'}</td>
                  <td>{formatContent(config)}</td>
                  <td>
                    <span
                      className={`importance-badge importance-${config.importance}`}
                    >
                      {IMPORTANCE_LABELS[config.importance] || config.importance}
                    </span>
                  </td>
                  <td className="admin-table-actions">
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => openEdit(config)}
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => handleDelete(config)}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {modalOpen && (
        <div className="modal-backdrop" onClick={closeModal} role="presentation">
          <div
            className="modal modal-wide"
            role="dialog"
            aria-modal="true"
            aria-labelledby="config-form-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="modal-header">
              <h2 id="config-form-title">
                {editing ? '编辑诊断配置' : '新增诊断配置'}
              </h2>
              <button
                type="button"
                className="modal-close"
                onClick={closeModal}
                disabled={submitting}
                aria-label="关闭"
              >
                ×
              </button>
            </header>

            <form className="modal-form" onSubmit={handleSubmit}>
              <label className="field">
                <span>诊断标题</span>
                <input
                  type="text"
                  value={form.title}
                  onChange={(e) => setField('title', e.target.value)}
                  required
                  maxLength={200}
                />
              </label>

              <label className="field">
                <span>诊断技巧</span>
                <textarea
                  rows={2}
                  value={form.technique}
                  onChange={(e) => setField('technique', e.target.value)}
                  placeholder="检查方法 / 给引擎的提示"
                />
              </label>

              <label className="field">
                <span>内容模式</span>
                <select
                  value={form.content_mode}
                  onChange={(e) => setField('content_mode', e.target.value)}
                >
                  <option value="full_text">全文 (full_text)</option>
                  <option value="description">内容描述 (description)</option>
                </select>
              </label>

              {form.content_mode === 'full_text' ? (
                <label className="field">
                  <span>内容范围</span>
                  <input
                    type="text"
                    value={form.content_scope}
                    onChange={(e) => setField('content_scope', e.target.value)}
                    placeholder="如 directory / body"
                  />
                </label>
              ) : (
                <label className="field">
                  <span>内容描述</span>
                  <textarea
                    rows={2}
                    value={form.content_text}
                    onChange={(e) => setField('content_text', e.target.value)}
                    placeholder="如：所有资质文件"
                  />
                </label>
              )}

              <label className="field">
                <span>重要性</span>
                <select
                  value={form.importance}
                  onChange={(e) => setField('importance', e.target.value)}
                >
                  <option value="high">高</option>
                  <option value="medium">中</option>
                  <option value="low">低</option>
                </select>
              </label>

              {formError && <p className="form-error">{formError}</p>}

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={closeModal}
                  disabled={submitting}
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={submitting}
                >
                  {submitting ? '保存中…' : '保存'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  )
}
