import type {
  PlaybookAutonomyMode,
  PlaybookLifecycleState,
} from '@/services/playbooks';

export const lifecycleLabel: Record<PlaybookLifecycleState, string> = {
  draft: 'Rascunho',
  in_review: 'Em revisão',
  approved: 'Aprovado',
  published: 'Publicado',
  superseded: 'Substituído',
};

export const lifecycleTone: Record<
  PlaybookLifecycleState,
  'gold' | 'blue' | 'green' | 'black' | 'muted'
> = {
  draft: 'muted',
  in_review: 'blue',
  approved: 'gold',
  published: 'green',
  superseded: 'black',
};

export const autonomyLabel: Record<PlaybookAutonomyMode, string> = {
  human_only: 'Somente humano',
  ai_triage_human_meeting: 'IA tria; humano reúne/fecha',
  ai_close_guarded: 'IA fecha com limites',
  ai_autonomous_supervised: 'IA autônoma supervisionada',
};

export const autonomyDescription: Record<PlaybookAutonomyMode, string> = {
  human_only: 'A IA resume e recomenda. Toda decisão comercial fica com a equipe.',
  ai_triage_human_meeting: 'A IA atende e qualifica; a política exige reunião e/ou fechamento por uma pessoa.',
  ai_close_guarded: 'A IA pode fechar ofertas previamente aprovadas dentro dos limites do playbook.',
  ai_autonomous_supervised: 'A IA executa ações reversíveis permitidas, com revisão e kill switch.',
};

export function formatPlaybookDate(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(new Date(value));
}
