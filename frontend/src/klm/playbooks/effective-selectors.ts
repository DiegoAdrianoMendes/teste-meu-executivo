import { getEffectivePlaybook } from '@/services/playbooks';

export type EffectivePlaybookSelectors = {
  area?: string | null;
  channel?: string | null;
  stage?: string | null;
  agent?: string | null;
  offer?: string | null;
};

const areaAliases: Record<string, string> = {
  direito_saude: 'negativas_plano_saude',
  health_plan_denials: 'negativas_plano_saude',
  saude_suplementar: 'negativas_plano_saude',
};

const agentAliases: Record<string, string> = {
  assistente_juridico: 'closer',
  juridico_saude: 'juridico',
  klm_analyst: 'juridico',
  klm_sdr: 'sdr',
  secretaria_hibrida: 'secretaria',
};

const stageAliases: Record<string, string> = {
  analise_juridica: 'qualificacao',
  crm_update: 'qualificacao',
  meeting_schedule: 'fechamento',
  proposta_enviada: 'proposta',
  triagem_sdr: 'triagem',
};

function normalize(value?: string | null): string | undefined {
  if (!value?.trim()) return undefined;
  return value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('pt-BR')
    .replace(/[^a-z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export function normalizeEffectivePlaybookSelectors(
  selectors: EffectivePlaybookSelectors,
): Parameters<typeof getEffectivePlaybook>[0] {
  const area = normalize(selectors.area);
  const agent = normalize(selectors.agent);
  const stage = normalize(selectors.stage);
  return {
    area: area ? areaAliases[area] ?? area : undefined,
    channel: normalize(selectors.channel),
    stage: stage ? stageAliases[stage] ?? stage : undefined,
    agent: agent ? agentAliases[agent] ?? agent : undefined,
    offer: normalize(selectors.offer),
  };
}
