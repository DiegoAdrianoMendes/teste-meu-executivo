import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Bot } from 'lucide-react';

const AGENT_LABELS: Record<string, { label: string; color: string }> = {
  klm_sdr: { label: 'SDR Jurídico', color: 'bg-violet-400/15 text-violet-500 border-violet-400' },
  klm_analyst: { label: 'Analista IA', color: 'bg-cyan-400/15 text-cyan-500 border-cyan-400' },
  klm_drafter: { label: 'Redator IA', color: 'bg-teal-400/15 text-teal-500 border-teal-400' },
  administrativo_ops: { label: 'Onboarding Ops', color: 'bg-emerald-400/15 text-emerald-500 border-emerald-400' },
  financeiro_ops: { label: 'Financeiro Ops', color: 'bg-orange-400/15 text-orange-500 border-orange-400' },
  secretaria_hibrida: { label: 'Secretaria Híbrida', color: 'bg-blue-400/15 text-blue-500 border-blue-400' },
  assistente_juridico: { label: 'Assistente Jurídico', color: 'bg-slate-400/15 text-slate-500 border-slate-400' },
};

export function AgentNode({ data, selected }: NodeProps) {
  const nodeData = (data && typeof data === 'object' ? data : {}) as Record<string, string | boolean>;
  const agent = typeof nodeData.agent === 'string' ? nodeData.agent : 'klm_sdr';
  const model = typeof nodeData.model === 'string' ? nodeData.model : '';
  const contextMode = typeof nodeData.context_mode === 'string' ? nodeData.context_mode : 'balanced';
  const humanReview = nodeData.human_review_required === true;
  const info = AGENT_LABELS[agent] ?? { label: agent, color: 'bg-violet-400/15 text-violet-500 border-violet-400' };
  return (
    <div className={`bg-card border-2 rounded-xl px-4 py-3 min-w-[190px] max-w-[240px] shadow-sm ${selected ? 'border-primary' : 'border-border'}`}>
      <Handle type="target" position={Position.Top} className="!bg-violet-400 !border-violet-600" />
      <div className="flex items-center gap-2">
        <div className={`w-7 h-7 shrink-0 rounded-lg flex items-center justify-center ${info.color.split(' ').slice(0,2).join(' ')}`}>
          <Bot className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-2xs text-muted-foreground uppercase tracking-wider">Agente IA</p>
          <p className="truncate text-xs font-semibold text-foreground" title={info.label}>{info.label}</p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1 pl-9">
        <span className="max-w-full truncate rounded-full bg-muted px-2 py-0.5 text-2xs text-muted-foreground">{contextMode}</span>
        {model ? <span className="max-w-full truncate rounded-full bg-muted px-2 py-0.5 text-2xs text-muted-foreground">{model}</span> : null}
        {humanReview ? <span className="max-w-full truncate rounded-full bg-amber-500/10 px-2 py-0.5 text-2xs text-amber-600">revisão humana</span> : null}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-violet-400 !border-violet-600" />
    </div>
  );
}
