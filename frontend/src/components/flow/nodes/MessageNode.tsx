import { Handle, Position, type NodeProps } from '@xyflow/react';
import { MessageSquare } from 'lucide-react';

export function MessageNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string>;
  const label = nodeData.label ?? 'Enviar mensagem';
  const preview = nodeData.message ?? '';
  return (
    <div className={`bg-card border-2 rounded-xl px-4 py-3 min-w-[180px] max-w-[220px] shadow-sm ${selected ? 'border-primary' : 'border-border'}`}>
      <Handle type="target" position={Position.Top} className="!bg-blue-400 !border-blue-600" />
      <div className="flex items-center gap-2 mb-1">
        <div className="w-7 h-7 shrink-0 rounded-lg bg-blue-400/15 flex items-center justify-center">
          <MessageSquare className="h-4 w-4 text-blue-500" />
        </div>
        <div className="min-w-0">
          <p className="text-2xs text-muted-foreground uppercase tracking-wider">Mensagem</p>
          <p className="truncate text-xs font-semibold text-foreground leading-tight" title={label}>{label}</p>
        </div>
      </div>
      {preview && <p className="text-2xs text-muted-foreground truncate mt-1 pl-9">{preview.slice(0, 50)}...</p>}
      <Handle type="source" position={Position.Bottom} className="!bg-blue-400 !border-blue-600" />
    </div>
  );
}
