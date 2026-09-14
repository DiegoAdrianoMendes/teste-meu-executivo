import { Bot, CheckCircle2, Clock3, LoaderCircle, XCircle } from 'lucide-react';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { KlmPill } from './Primitives';

export type AgentRunDrawerStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'waiting_human' | 'waiting_data' | 'waiting_tool' | 'waiting_external' | 'paused_budget' | string;

export interface AgentRunDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentName: string;
  status: AgentRunDrawerStatus;
  summary?: string;
  events?: Array<{ id: string; label: string; detail?: string; at?: string }>;
}

const statusCopy: Record<string, string> = { queued: 'Na fila', running: 'Executando', completed: 'Concluído', failed: 'Falhou', cancelled: 'Cancelado', waiting_human: 'Aguardando humano', waiting_data: 'Aguardando dados', waiting_tool: 'Aguardando ferramenta', waiting_external: 'Aguardando confirmação externa', paused_budget: 'Pausado por orçamento' };

export function AgentRunDrawer({ open, onOpenChange, agentName, status, summary, events = [] }: AgentRunDrawerProps) {
  const running = status === 'running';
  const waitingHuman = status === 'waiting_human';
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent side="right" className="w-full sm:max-w-md">
      <SheetHeader className="pr-10 text-left"><SheetTitle className="flex items-center gap-2"><Bot className="h-5 w-5 text-primary" />Run de {agentName}</SheetTitle><SheetDescription>Trilha local do agente. Nenhuma ação externa é executada por este painel.</SheetDescription></SheetHeader>
      <div className="mt-5 space-y-4">
        <div className="flex items-center justify-between rounded-lg border p-3"><span className="text-xs text-muted-foreground">Status</span><KlmPill tone={running ? 'gold' : status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'muted'}>{statusCopy[status] ?? status}</KlmPill></div>
        {summary && <p className="rounded-lg bg-secondary p-3 text-sm leading-5">{summary}</p>}
        {waitingHuman ? <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-muted-foreground">A execução está em checkpoint humano. A decisão vinculada é resolvida pela Central de Decisões; este painel não cria uma chamada manual nem retoma outro run.</p> : null}
        <div className="space-y-2" aria-label="Eventos do run">{events.map((event) => <div key={event.id} className="flex gap-3 rounded-lg border p-3"><span className="mt-0.5 shrink-0">{running ? <LoaderCircle className="h-4 w-4 animate-spin text-primary" /> : status === 'failed' ? <XCircle className="h-4 w-4 text-red-500" /> : status === 'completed' ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Clock3 className="h-4 w-4 text-muted-foreground" />}</span><span className="min-w-0"><strong className="block text-xs">{event.label}</strong>{event.detail && <span className="mt-1 block text-xs text-muted-foreground">{event.detail}</span>}{event.at && <time className="mt-1 block text-[10px] text-muted-foreground">{event.at}</time>}</span></div>)}</div>
      </div>
    </SheetContent>
  </Sheet>;
}
