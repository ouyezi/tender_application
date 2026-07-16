import ReactMarkdown from 'react-markdown'

export default function MarkdownPreview({ markdown }) {
  if (!markdown) return null

  return (
    <div className="markdown-preview">
      <ReactMarkdown
        components={{
          img: ({ src, alt, ...props }) => (
            <img
              src={src}
              alt={alt || ''}
              loading="lazy"
              className="markdown-preview-img"
              {...props}
            />
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
