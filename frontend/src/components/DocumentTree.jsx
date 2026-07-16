import { useState } from 'react'

function TreeNode({ node, depth, selectedId, onSelect }) {
  const children = Array.isArray(node.children) ? node.children : []
  const hasChildren = children.length > 0
  const [collapsed, setCollapsed] = useState(false)
  const isSelected = node.id === selectedId

  return (
    <li className="tree-node">
      <div
        className={`tree-node-row${isSelected ? ' tree-node-row-selected' : ''}`}
        style={{ paddingLeft: `${depth * 0.9}rem` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="tree-toggle"
            onClick={() => setCollapsed((prev) => !prev)}
            aria-label={collapsed ? '展开' : '收起'}
            aria-expanded={!collapsed}
          >
            {collapsed ? '▸' : '▾'}
          </button>
        ) : (
          <span className="tree-toggle tree-toggle-spacer" aria-hidden="true" />
        )}
        <button
          type="button"
          className="tree-node-title"
          onClick={() => onSelect?.(node)}
          title={node.title}
        >
          {node.numbering ? `${node.numbering} ` : ''}
          {node.title || '(未命名)'}
        </button>
      </div>
      {hasChildren && !collapsed && (
        <ul className="tree-children">
          {children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

export default function DocumentTree({ nodes, selectedId, onSelect }) {
  const items = Array.isArray(nodes) ? nodes : []

  if (items.length === 0) {
    return <p className="empty-state-hint">暂无目录结构</p>
  }

  return (
    <ul className="document-tree">
      {items.map((node) => (
        <TreeNode
          key={node.id}
          node={node}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      ))}
    </ul>
  )
}
