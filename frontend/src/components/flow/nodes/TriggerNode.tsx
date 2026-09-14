import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Zap } from 'lucide-react';

export function TriggerNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string>;
  const label = nodeData.label ?? 'Trigger';
  return (
    <div className={`bg-card border-2 rounded-xl px-4 py-3 min-w-[160px] max-w-[230px] shadow-sm ${selected ? 'border-primary' : 'border-yellow-400'}`}>
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 shrink-0 rounded-lg bg-yellow-400/15 flex items-center justify-center">
          <Zap className="h-4 w-4 text-yellow-500" />
        </div>
        <div className="min-w-0">
          <p className="text-2xs text-muted-foreground uppercase tracking-wider">Trigger</p>
          <p className="truncate text-xs font-semibold text-foreground leading-tight" title={label}>{label}</p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-yellow-400 !border-yellow-600" />
    </div>
  );
}
