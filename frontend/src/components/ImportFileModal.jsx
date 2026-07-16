import { useState } from 'react'
import { importWorkspaceFile } from '../api'

export default function ImportFileModal({ taskId, open, onClose, onSuccess }) {
  const [file, setFile] = useState(null)
  const [label, setLabel] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  function resetForm() {
    setFile(null)
    setLabel('')
    setError('')
    setSubmitting(false)
  }

  function handleClose() {
    if (submitting) return
    resetForm()
    onClose?.()
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')

    if (!file) {
      setError('请选择文件')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    formData.append('label', label)

    setSubmitting(true)
    try {
      const wf = await importWorkspaceFile(taskId, formData)
      resetForm()
      onClose?.()
      onSuccess?.(wf)
    } catch (err) {
      setError(err.message || '导入失败，请重试')
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={handleClose} role="presentation">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-file-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2 id="import-file-title">导入文件</h2>
          <button
            type="button"
            className="modal-close"
            onClick={handleClose}
            disabled={submitting}
            aria-label="关闭"
          >
            ×
          </button>
        </header>

        <form className="modal-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>文件</span>
            <input
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              required
            />
          </label>

          <label className="field">
            <span>标签</span>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="可选：为文件添加说明标签"
            />
          </label>

          {error && <p className="form-error">{error}</p>}

          <div className="modal-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleClose}
              disabled={submitting}
            >
              取消
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? '上传中…' : '导入'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
