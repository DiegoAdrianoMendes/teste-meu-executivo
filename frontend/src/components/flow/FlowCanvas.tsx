import { useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Node,
  type Edge,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { TriggerNode } from './nodes/TriggerNode';
import { MessageNode } from './nodes/MessageNode';
import { WaitNode } from './nodes/WaitNode';
import { ConditionNode } from './nodes/ConditionNode';
import { HumanPauseNode } from './nodes/HumanPauseNode';
import { AgentNode } from './nodes/AgentNode';
import { ZapSignNode } from './nodes/ZapSignNode';

const nodeTypes = {
  trigger: TriggerNode,
  send_message: MessageNode,
  wait: WaitNode,
  condition: ConditionNode,
  human_pause: HumanPauseNode,
  agent: AgentNode,
  zapsign_request: ZapSignNode,
};

interface FlowCanvasProps {
  initialNodes: Node[];
  initialEdges: Edge[];
  onNodesChange?: (nodes: Node[]) => void;
  onEdgesChange?: (edges: Edge[]) => void;
  onNodeSelect?: (node: Node | null) => void;
  readOnly?: boolean;
}

export function FlowCanvas({
  initialNodes,
  initialEdges,
  onNodesChange: onNodesChangeProp,
  onEdgesChange: onEdgesChangeProp,
  onNodeSelect,
  readOnly = false,
}: FlowCanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  const onConnect = useCallback(
    (params: Connection) => {
      if (readOnly) return;
      setEdges((eds) => addEdge({ ...params, animated: true }, eds));
    },
    [readOnly, setEdges]
  );

  const handleNodesChange = useCallback(
    (changes: Parameters<typeof onNodesChange>[0]) => {
      onNodesChange(changes);
      if (onNodesChangeProp) {
        setNodes((nds) => { onNodesChangeProp(nds); return nds; });
      }
    },
    [onNodesChange, onNodesChangeProp, setNodes]
  );

  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof onEdgesChange>[0]) => {
      onEdgesChange(changes);
      if (onEdgesChangeProp) {
        setEdges((eds) => { onEdgesChangeProp(eds); return eds; });
      }
    },
    [onEdgesChange, onEdgesChangeProp, setEdges]
  );

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        snapToGrid
        snapGrid={[16, 16]}
        fitView
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable={!readOnly}
        onNodeClick={(_, node) => onNodeSelect?.(node)}
        onPaneClick={() => onNodeSelect?.(null)}
        className="bg-background"
        defaultEdgeOptions={{ animated: true, style: { strokeWidth: 2, stroke: 'hsl(var(--border))' } }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="hsl(var(--border))" />
        <Controls showInteractive={!readOnly} />
        <MiniMap
          nodeColor={(n) => {
            const map: Record<string, string> = {
              trigger: '#facc15', send_message: '#60a5fa', wait: '#94a3b8',
              condition: '#fbbf24', human_pause: '#fb923c', agent: '#a78bfa', zapsign_request: '#34d399'
            };
            return map[n.type ?? ''] ?? '#94a3b8';
          }}
          className="!bg-card !border !border-border"
        />
      </ReactFlow>
    </div>
  );
}
