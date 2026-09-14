import type { KlmMatterFlowJourneyOverview } from '@/klm/api';

// V26: 2,5 s só faz sentido com run em execução. Esperando humano, 15 s; ocioso, 60 s.
// Sem isso toda conversa aberta fazia 24 GET/min mesmo sem fluxo no caso.
export const LIVE_REFRESH_MS = 2_500;
export const WAITING_HUMAN_REFRESH_MS = 15_000;
export const IDLE_REFRESH_MS = 60_000;
export const LIVE_LABEL = `Ao vivo · ${(LIVE_REFRESH_MS / 1000).toLocaleString('pt-BR')}s`;
export const WAITING_HUMAN_LABEL = `Aguardando humano · ${WAITING_HUMAN_REFRESH_MS / 1000}s`;

export function refreshIntervalFor(overview: KlmMatterFlowJourneyOverview | undefined): number {
  const kpis = overview?.kpis;
  if ((kpis?.active_runs ?? 0) > 0) return LIVE_REFRESH_MS;
  if ((kpis?.waiting_human_runs ?? 0) > 0) return WAITING_HUMAN_REFRESH_MS;
  return IDLE_REFRESH_MS;
}
