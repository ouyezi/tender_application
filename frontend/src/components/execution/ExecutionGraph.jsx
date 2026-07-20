import { useMemo } from 'react'
import { ReactFlow, Background, Controls } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'
import ExecutionNodeCard from './ExecutionNodeCard.jsx'

const nodeTypes = { execution: ExecutionNodeCard }

function layoutWithDagre(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 60 })
  nodes.forEach((n) => g.setNode(n.id, { width: 180, height: 56 }))
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map((n) => {
    const { x, y } = g.node(n.id)
    return { ...n, position: { x, y } }
  })
}

export default function ExecutionGraph({ graph, selectedKey, onSelectNode }) {
  const { nodes, edges } = useMemo(() => {
    const rfNodes = graph.nodes.map((n) => ({
      id: n.key,
      type: 'execution',
      data: {
        label: n.label,
        status: n.status,
        duration_ms: n.duration_ms,
        selected: n.key === selectedKey,
      },
      position: { x: 0, y: 0 },
    }))
    const rfEdges = graph.edges.map((e, i) => ({
      id: `${e.from}-${e.to}-${i}`,
      source: e.from,
      target: e.to,
    }))
    return { nodes: layoutWithDagre(rfNodes, rfEdges), edges: rfEdges }
  }, [graph, selectedKey])

  return (
    <div className="execution-graph-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        onNodeClick={(_, node) => onSelectNode(node.id)}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}
