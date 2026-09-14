import type { Agent } from '@/types';

const SYSTEM_AGENT_LABELS: Record<string, { name: string; role: string }> = {
  orquestrador: { name: 'Orquestrador KLM', role: 'Coordenação de conversas e sistema' },
  hermes: { name: 'Hermes', role: 'Ponte executiva com o dono' },
  klm_sdr: { name: 'KLM SDR', role: 'Triagem comercial' },
  klm_analyst: { name: 'KLM Jurídico', role: 'Análise jurídica assistida' },
  klm_drafter: { name: 'KLM Petições', role: 'Redação jurídica assistida' },
  klm_onboarding_ops: { name: 'KLM Paralegal', role: 'Coleta e onboarding' },
};

function initialsFor(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase() || 'US';
}

function isAutomatedAgent(id: string): boolean {
  return id.startsWith('klm_')
    || id.startsWith('agent:')
    || /(^|[-_:])(bot|sdr|analyst|drafter|assistant|assistente)([-_:]|$)/i.test(id);
}

function fallbackName(id: string, automated: boolean): string {
  const compact = id.trim();
  if (/^[0-9a-f-]{24,}$/i.test(compact)) return `Usuário ${compact.slice(0, 8)}`;
  if (/^ag\d+$/i.test(compact)) return `Usuário ${compact.toUpperCase()}`;
  if (!automated) return compact ? `Usuário ${compact}` : 'Usuário';

  const cleaned = compact.replace(/^agent:/, '').replace(/[_-]+/g, ' ').trim();
  return cleaned
    ? cleaned.split(/\s+/).map((part) => part[0].toUpperCase() + part.slice(1)).join(' ')
    : 'Agente KLM';
}

export function getAgentById(id: string): Agent | undefined {
  if (!id) return undefined;

  const known = SYSTEM_AGENT_LABELS[id];
  const automated = Boolean(known) || isAutomatedAgent(id);
  const name = known?.name ?? fallbackName(id, automated);
  return {
    id,
    name,
    type: automated ? 'ai' : 'human',
    initials: initialsFor(name),
    role: known?.role ?? (automated ? 'Agente automatizado' : 'Usuário do workspace'),
    color: automated ? 'hsl(220 7% 46%)' : 'hsl(151 42% 30%)',
  };
}
