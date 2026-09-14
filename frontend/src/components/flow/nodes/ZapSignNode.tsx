import { Handle, Position, type NodeProps } from '@xyflow/react';
import { FileSignature } from 'lucide-react';

export function ZapSignNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string>;
  const docType = nodeData.doc_type ?? 'Procuração';
  const provider = nodeData.provider ?? 'zapsign';
  return (
    <div className={`bg-card border-2 rounded-xl px-4 py-3 min-w-[180px] shadow-sm ${selected ? 'border-primary' : 'border-emerald-400'}`}>
      <Handle type="target" position={Position.Top} className="!bg-emerald-400 !border-emerald-600" />
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 shrink-0 rounded-lg bg-emerald-400/15 flex items-center justify-center">
          <FileSignature className="h-4 w-4 text-emerald-500" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-2xs text-muted-foreground uppercase tracking-wider">{provider}</p>
          <p className="truncate text-xs font-semibold text-foreground capitalize" title={docType}>{docType}</p>
        </div>
      </div>
      {nodeData.auth_mode ? (
        <p className="mt-2 pl-9 text-2xs text-muted-foreground truncate">{nodeData.auth_mode}</p>
      ) : null}
      <Handle type="source" position={Position.Bottom} className="!bg-emerald-400 !border-emerald-600" />
    </div>
  );
}
