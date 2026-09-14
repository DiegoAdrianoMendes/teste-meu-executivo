import { Handle, Position, type NodeProps } from '@xyflow/react';
import { GitBranch } from 'lucide-react';

export function ConditionNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string>;
  const condition = nodeData.condition ?? 'Condição';
  return (
    <div className={`bg-card border-2 rounded-xl px-4 py-3 min-w-[170px] shadow-sm ${selected ? 'border-primary' : 'border-amber-400'}`}>
      <Handle type="target" position={Position.Top} className="!bg-amber-400 !border-amber-600" />
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 shrink-0 rounded-lg bg-amber-400/15 flex items-center justify-center">
          <GitBranch className="h-4 w-4 text-amber-500" />
        </div>
        <div className="min-w-0">
          <p className="text-2xs text-muted-foreground uppercase tracking-wider">Condição</p>
          <p className="text-xs font-semibold text-foreground leading-tight truncate max-w-[120px]" title={condition}>{condition}</p>
        </div>
      </div>
      <div className="flex justify-between mt-2 text-2xs text-muted-foreground px-1">
        <span className="text-green-500 font-semibold">SIM ↙</span>
        <span className="text-red-500 font-semibold">↘ NÃO</span>
      </div>
      <Handle type="source" position={Position.Bottom} id="true" style={{ left: '25%' }} className="!bg-green-400 !border-green-600" />
      <Handle type="source" position={Position.Bottom} id="false" style={{ left: '75%' }} className="!bg-red-400 !border-red-600" />
    </div>
  );
}
