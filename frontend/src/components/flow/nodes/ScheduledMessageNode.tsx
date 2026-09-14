import { Handle, Position, type NodeProps } from '@xyflow/react';
import { CalendarClock } from 'lucide-react';

export function ScheduledMessageNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string>;
  const label = nodeData.label ?? 'Mensagem agendada';
  const scheduledAt = nodeData.scheduled_at ?? '09:00';
  const preview = nodeData.message ?? '';

  return (
    <div className={`min-w-[190px] max-w-[230px] rounded-xl border-2 bg-card px-4 py-3 shadow-sm ${selected ? 'border-primary' : 'border-cyan-400'}`}>
      <Handle type="target" position={Position.Top} className="!border-cyan-600 !bg-cyan-400" />
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-cyan-400/15">
          <CalendarClock className="h-4 w-4 text-cyan-600" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-2xs uppercase tracking-wider tabular-nums text-muted-foreground">Agendada · {scheduledAt}</p>
          <p className="truncate text-xs font-semibold text-foreground" title={label}>{label}</p>
        </div>
      </div>
      {preview ? <p className="mt-2 truncate pl-9 text-2xs text-muted-foreground">{preview}</p> : null}
      <Handle type="source" position={Position.Bottom} className="!border-cyan-600 !bg-cyan-400" />
    </div>
  );
}
