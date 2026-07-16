import { useState } from 'react'
import { createTask } from '../api'

export default function CreateTaskModal({ open, onClose, onCreated }) {
  const [tenderFile, setTenderFile] = useState(null)
  const [bidFile, setBidFile] = useState(null)
  const [background, setBackground] = useState('')
  const [requirements, setRequirements] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  if (!open) return null

  function resetForm() {
    setTenderFile(null)
    setBidFile(null)
    setBackground('')
    setRequirements('')
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

    if (!tenderFile) {
      setError('请选择招标文件')
      return
    }
    if (!bidFile) {
      setError('请选择投标文件')
      return
    }

    const formData = new FormData()
    formData.append('tender_file', tenderFile)
    formData.append('bid_file', bidFile)
    formData.append('background', background)
    formData.append('requirements', requirements)

    setSubmitting(true)
    try {
      const task = await createTask(formData)
      resetForm()
      onClose?.()
      onCreated?.(task)
    } catch (err) {
      setError(err.message || '创建失败，请重试')
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={handleClose} role="presentation">
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-task-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <h2 id="create-task-title">创建诊断</h2>
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
            <span>招标文件</span>
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={(e) => setTenderFile(e.target.files?.[0] || null)}
              required
            />
          </label>

          <label className="field">
            <span>投标文件</span>
            <input
              type="file"
              accept=".pdf,.doc,.docx"
              onChange={(e) => setBidFile(e.target.files?.[0] || null)}
              required
            />
          </label>

          <label className="field">
            <span>项目背景</span>
            <textarea
              rows={3}
              value={background}
              onChange={(e) => setBackground(e.target.value)}
              placeholder="可选：简要说明项目背景"
            />
          </label>

          <label className="field">
            <span>诊断要求</span>
            <textarea
              rows={3}
              value={requirements}
              onChange={(e) => setRequirements(e.target.value)}
              placeholder="可选：补充特别关注的诊断要求"
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
              {submitting ? '提交中…' : '开始诊断'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
