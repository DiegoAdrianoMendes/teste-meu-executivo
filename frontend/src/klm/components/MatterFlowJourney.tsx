import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Check,
  ChevronDown,
  Circle,
  CircleAlert,
  Clock3,
  LoaderCircle,
  Pause,
  RotateCw,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { klmApi, type KlmMatterFlowJourney, type KlmMatterFlowJourneyStep } from '@/klm/api';
import { LIVE_LABEL, WAITING_HUMAN_LABEL, refreshIntervalFor } from '@/klm/lib/flowJourneyPolling';
import { KlmPill, KlmProgress } from '@/klm/components/Primitives';
import { cn } from '@/lib/utils';


const RUN_STATUS: Record<KlmMatterFlowJourney['status'], { label: string; tone: 'green' | 'gold' | 'red' | 'blue' | 'muted' }> = {
  active: { label: 'Em execução', tone: 'blue' },
  paused: { label: 'Pausado', tone: 'gold' },
  completed: { label: 'Concluído', tone: 'green' },
  failed: { label: 'Falhou', tone: 'red' },
  waiting_human: { label: 'Aguardando humano', tone: 'gold' },
  cancelled: { label: 'Cancelado', tone: 'muted' },
};

const STEP_STATUS: Record<KlmMatterFlowJourneyStep['state'], string> = {
  completed: 'Concluído',
  current: 'Em execução',
  upcoming: 'Próximo passo',
  failed: 'Falhou',
  waiting_human: 'Aguardando humano',
};

function durationLabel(seconds: number | null): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3_600) return `${Math.floor(seconds / 60)}min`;
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  return minutes ? `${hours}h ${minutes}min` : `${hours}h`;
}

function StepIcon({ state }: { state: KlmMatterFlowJourneyStep['state'] }) {
  if (state === 'completed') return <Check className="h-3 w-3" />;
  if (state === 'failed') return <CircleAlert className="h-3 w-3" />;
  if (state === 'waiting_human') return <Pause className="h-3 w-3" />;
  if (state === 'current') return <LoaderCircle className="h-3 w-3 animate-spin" />;
  return <Circle className="h-2.5 w-2.5" />;
}

function FlowTimeline({ journey }: { journey: KlmMatterFlowJourney }) {
  const status = RUN_STATUS[journey.status] ?? RUN_STATUS.paused;
  const active = ['active', 'paused', 'waiting_human'].includes(journey.status);
  const [isOpen, setIsOpen] = useState(active);
  return (
    <details
      className="group border-t border-border py-3 first:border-t-0"
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary className="flex cursor-pointer list-none items-start gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <strong className="min-w-0 flex-1 truncate text-2xs">{journey.flow_name}</strong>
            <KlmPill tone={status.tone} className="shrink-0">{status.label}</KlmPill>
          </div>
          <div className="mt-2 flex items-center justify-between gap-3 text-2xs text-muted-foreground">
            <span className="min-w-0 truncate">
              {journey.completed_steps}/{journey.total_steps} etapas · rev. {journey.revision} · {new Date(journey.started_at).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })} · {durationLabel(journey.elapsed_seconds)}
            </span>
            <strong className="shrink-0 text-foreground">{journey.progress_percent.toLocaleString('pt-BR')}%</strong>
          </div>
          <div className="mt-1.5"><KlmProgress value={journey.progress_percent} tone={journey.status === 'failed' ? 'red' : journey.status === 'completed' ? 'green' : 'gold'} /></div>
          {journey.current_step_label ? <p className="mt-2 line-clamp-2 text-2xs leading-4 text-muted-foreground"><span className="font-semibold text-foreground">Agora:</span> {journey.current_step_label}</p> : null}
        </div>
        <ChevronDown className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="mt-3 pl-1">
        {journey.pause_reason ? <p className="mb-3 border-l-2 border-primary pl-2 text-2xs leading-4 text-muted-foreground">{journey.pause_reason}</p> : null}
        {journey.error_message ? <p className="mb-3 border-l-2 border-red-500 pl-2 text-2xs leading-4 text-[#a53e3c] dark:text-red-400">{journey.error_message}</p> : null}
        <div className="space-y-0">
          {journey.timeline.map((step, index) => (
            <div key={`${journey.run_id}-${step.step ?? index}-${step.node_id}`} className="grid grid-cols-[18px_minmax(0,1fr)] gap-2">
              <div className="relative flex justify-center">
                {index < journey.timeline.length - 1 ? <span className="absolute bottom-0 top-4 w-px bg-border" /> : null}
                <span className={cn(
                  'relative z-10 flex h-4 w-4 items-center justify-center rounded-full border bg-card',
                  step.state === 'completed' && 'border-emerald-600 bg-emerald-600 text-white',
                  step.state === 'failed' && 'border-red-600 bg-red-600 text-white',
                  step.state === 'current' && 'border-[#031121] bg-[#031121] text-white dark:border-blue-400 dark:bg-blue-400 dark:text-black',
                  step.state === 'waiting_human' && 'border-primary bg-primary text-black',
                  step.state === 'upcoming' && 'border-border text-muted-foreground',
                )}><StepIcon state={step.state} /></span>
              </div>
              <div className="min-w-0 pb-3">
                <div className="flex min-w-0 items-start justify-between gap-2">
                  <p className={cn('min-w-0 break-words text-2xs leading-4', step.state === 'upcoming' ? 'text-muted-foreground' : 'font-medium')}>{step.label}</p>
                  <span className="shrink-0 text-2xs text-muted-foreground">{STEP_STATUS[step.state]}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-2xs text-muted-foreground">
                  <span>Etapa {step.step ?? index + 1}</span>
                  {step.occurred_at ? <span>{new Date(step.occurred_at).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}</span> : null}
                  {step.decision?.branch ? <span>Ramo {step.decision.branch === 'true' ? 'sim' : 'não'}</span> : null}
                </div>
              </div>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between border-t border-border pt-2 text-2xs text-muted-foreground">
          <span>Iniciado {new Date(journey.started_at).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}</span>
          <span>{durationLabel(journey.elapsed_seconds)}</span>
        </div>
      </div>
    </details>
  );
}

export function MatterFlowJourney({ matterId }: { matterId: string }) {
  const journeyQuery = useQuery({
    queryKey: ['klm', 'matter-flow-journeys', matterId],
    queryFn: () => klmApi.matterFlowJourneys(matterId),
    refetchInterval: (query) => refreshIntervalFor(query.state.data),
    refetchIntervalInBackground: false,
    staleTime: 2_000,
    retry: 1,
  });
  const overview = journeyQuery.data;
  const kpis = overview?.kpis;
  const liveRuns = (kpis?.active_runs ?? 0) > 0;
  const waitingHuman = !liveRuns && (kpis?.waiting_human_runs ?? 0) > 0;
  const active = liveRuns || waitingHuman;

  if (journeyQuery.isPending) {
    return <p className="flex items-center gap-2 text-2xs text-muted-foreground"><LoaderCircle className="h-3.5 w-3.5 animate-spin" />Carregando jornada dos fluxos</p>;
  }
  if (journeyQuery.isError || !overview) {
    return (
      <div className="flex items-center justify-between gap-3 text-2xs">
        <span className="text-[#a53e3c] dark:text-red-400">A jornada não pôde ser carregada.</span>
        <Button type="button" variant="outline" size="sm" className="h-7 px-2 text-2xs" onClick={() => void journeyQuery.refetch()}><RotateCw className="mr-1 h-3 w-3" />Recarregar</Button>
      </div>
    );
  }

  return (
    <div data-testid="matter-flow-journey" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between text-2xs"><span className="text-muted-foreground">Progresso geral</span><strong>{kpis?.overall_progress_percent.toLocaleString('pt-BR') ?? '0'}%</strong></div>
          <div className="mt-1"><KlmProgress value={kpis?.overall_progress_percent ?? 0} tone={(kpis?.failed_runs ?? 0) > 0 ? 'red' : (kpis?.overall_progress_percent ?? 0) === 100 ? 'green' : 'gold'} /></div>
        </div>
        <span className={cn('flex shrink-0 items-center gap-1 text-2xs font-semibold', active ? 'text-[#0b7435] dark:text-emerald-400' : 'text-muted-foreground')}>
          <span className={cn('h-1.5 w-1.5 rounded-full', active ? 'animate-pulse bg-emerald-600' : 'bg-muted-foreground/40')} />
          {liveRuns ? LIVE_LABEL : waitingHuman ? WAITING_HUMAN_LABEL : 'Monitorado'}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-3 border-y border-border py-2 text-center">
        <div><dt className="text-2xs text-muted-foreground">Ativos</dt><dd className="mt-0.5 text-xs font-semibold">{kpis?.active_runs ?? 0}</dd></div>
        <div className="border-x border-border"><dt className="text-2xs text-muted-foreground">Humano</dt><dd className="mt-0.5 text-xs font-semibold">{kpis?.waiting_human_runs ?? 0}</dd></div>
        <div><dt className="text-2xs text-muted-foreground">Sucesso</dt><dd className="mt-0.5 text-xs font-semibold">{kpis?.completion_rate.toLocaleString('pt-BR') ?? '0'}%</dd></div>
      </dl>
      <dl className="grid grid-cols-3 border-b border-border py-2 text-center">
        <div><dt className="text-2xs text-muted-foreground">Etapas</dt><dd className="mt-0.5 text-2xs font-semibold">{kpis?.completed_steps ?? 0}/{kpis?.total_steps ?? 0}</dd></div>
        <div className="border-x border-border"><dt className="text-2xs text-muted-foreground">Falhas</dt><dd className={cn('mt-0.5 text-2xs font-semibold', (kpis?.failed_runs ?? 0) > 0 && 'text-[#a53e3c] dark:text-red-400')}>{kpis?.failed_runs ?? 0}</dd></div>
        <div><dt className="text-2xs text-muted-foreground">Tempo médio</dt><dd className="mt-0.5 text-2xs font-semibold">{durationLabel(kpis?.average_duration_seconds ?? null)}</dd></div>
      </dl>

      {overview.journeys.length ? (
        <div className="mt-1">{overview.journeys.map((journey) => <FlowTimeline key={journey.run_id} journey={journey} />)}</div>
      ) : (
        <div className="py-4 text-center">
          <Clock3 className="mx-auto h-4 w-4 text-muted-foreground" />
          <p className="mt-2 text-2xs font-medium">Nenhum fluxo executado neste caso</p>
          <p className="mt-1 text-2xs leading-4 text-muted-foreground">Ao ativar um fluxo, os passos e o percentual aparecerão aqui.</p>
        </div>
      )}
    </div>
  );
}

export default MatterFlowJourney;
