import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Clock } from 'lucide-react';

export function WaitNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const hours = typeof nodeData.delay_hours === 'number' ? nodeData.delay_hours : 24;
  const label = hours >= 24 ? `${hours / 24} dia${hours / 24 > 1 ? 's' : ''}` : `${hours}h`;
  return (
    <div className={`bg-card border-2 rounded-xl px-4 py-3 min-w-[150px] shadow-sm ${selected ? 'border-primary' : 'border-border'}`}>
      <Handle type="target" position={Position.Top} className="!bg-slate-400 !border-slate-600" />
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 shrink-0 rounded-lg bg-slate-400/15 flex items-center justify-center">
          <Clock className="h-4 w-4 text-slate-500" />
        </div>
        <div className="min-w-0">
          <p className="text-2xs text-muted-foreground uppercase tracking-wider">Aguardar</p>
          <p className="truncate text-xs font-semibold tabular-nums text-foreground">{label}</p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400 !border-slate-600" />
    </div>
  );
}
