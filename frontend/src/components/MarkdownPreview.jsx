import ReactMarkdown from 'react-markdown'

export default function MarkdownPreview({ markdown }) {
  if (!markdown) return null

  return (
    <div className="markdown-preview">
      <ReactMarkdown>{markdown}</ReactMarkdown>
    </div>
  )
}
