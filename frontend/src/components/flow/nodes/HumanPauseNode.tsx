import { Handle, Position, type NodeProps } from '@xyflow/react';
import { UserCheck } from 'lucide-react';

export function HumanPauseNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string>;
  const label = nodeData.label ?? 'Aprovação humana';
  const reason = nodeData.reason ?? 'Revisão humana';
  return (
    <div className={`min-w-[200px] max-w-[240px] rounded-lg border-2 bg-card px-4 py-3 shadow-sm ${selected ? 'border-primary' : 'border-orange-400'}`}>
      <Handle type="target" position={Position.Top} className="!bg-orange-400 !border-orange-600" />
      <div className="flex items-start gap-2">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-orange-400/15">
          <UserCheck className="h-4 w-4 text-orange-500" />
        </div>
        <div className="min-w-0">
          <p className="text-2xs uppercase tracking-wider text-muted-foreground">Pausa humana</p>
          <p className="truncate text-xs font-semibold leading-tight text-foreground" title={label}>{label}</p>
          <p className="mt-1 line-clamp-2 text-2xs text-muted-foreground" title={reason}>{reason}</p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-orange-400 !border-orange-600" />
    </div>
  );
}
