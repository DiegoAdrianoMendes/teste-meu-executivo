import type { Filters, MatterCard } from '@/types';
import type { PersonalizedFilterField } from '@/lib/personalized-filters';

function normalized(value: unknown): string {
  return String(value ?? '').trim().toLocaleLowerCase('pt-BR');
}

function includesAny<T>(selected: T[], values: T[]): boolean {
  return selected.length === 0 || selected.some((value) => values.includes(value));
}

function searchableMatterText(card: MatterCard): string {
  return normalized([
    card.client.name,
    card.client.email,
    card.client.phone,
    card.client.employer,
    card.client.position,
    card.caseNumber,
    card.demandType,
    card.healthObjectName,
    card.healthInsurer,
    card.healthDenialReason,
    card.leadSource,
    card.executiveSummary,
    card.strategyNotes,
    card.nextAction,
    card.conversationInsight?.summary,
    card.conversationInsight?.recommendedAction,
    ...card.risks,
    ...card.blocks,
    ...card.documents.map((document) => document.name),
    ...card.conversations.map((conversation) => conversation.message),
  ].join(' '));
}

export function filterMatterCards(
  cards: MatterCard[],
  filters: Filters,
  now = new Date(),
): MatterCard[] {
  const query = normalized(filters.search);
  const updatedCutoff = filters.updatedWithinDays === null
    ? null
    : now.getTime() - filters.updatedWithinDays * 24 * 60 * 60 * 1_000;

  return cards.filter((card) => {
    if (query && !searchableMatterText(card).includes(query)) return false;
    if (!includesAny(filters.status, [card.microStatus])) return false;
    if (!includesAny(filters.priority, [card.priority])) return false;
    if (!includesAny(filters.urgency, [card.urgency])) return false;
    if (!includesAny(filters.columnId, [card.columnId])) return false;
    if (!includesAny(filters.classification, [card.classification])) return false;
    if (!includesAny(filters.leadSource, [card.leadSource ?? ''])) return false;
    if (!includesAny(filters.closeIntent, [card.conversationInsight?.closeIntent ?? ''])) return false;
    if (!includesAny(filters.documentReadiness, [card.conversationInsight?.documentReadiness ?? ''])) return false;

    const agents = [card.responsibleHuman, card.currentAgent, ...card.assignedAgents];
    if (!includesAny(filters.agentId, agents)) return false;
    if (filters.isBlocked !== null && card.isBlocked !== filters.isBlocked) return false;
    if (filters.needsApproval !== null && card.needsApproval !== filters.needsApproval) return false;
    if (filters.hasUnreadMessages !== null && (card.unreadMessages > 0) !== filters.hasUnreadMessages) return false;
    if (filters.hasFinancialPending !== null && (card.microStatus === 'pagamento_pendente') !== filters.hasFinancialPending) return false;
    if (filters.docScoreBelow !== null && !(card.docScore < filters.docScoreBelow)) return false;
    if (filters.minClaimValue !== null && (card.claimValue ?? 0) < filters.minClaimValue) return false;
    if (filters.maxClaimValue !== null && (card.claimValue ?? 0) > filters.maxClaimValue) return false;
    if (updatedCutoff !== null) {
      const updatedAt = new Date(card.updatedAt || card.lastInteraction).getTime();
      if (!Number.isFinite(updatedAt) || updatedAt < updatedCutoff) return false;
    }
    return true;
  });
}

const options = (values: string[]) => [...new Set(values.filter(Boolean))]
  .sort((left, right) => left.localeCompare(right, 'pt-BR'))
  .map((value) => ({ value, label: value.replaceAll('_', ' ') }));

export function matterCardPersonalizedFields(cards: MatterCard[]): PersonalizedFilterField<MatterCard>[] {
  return [
    { key: 'client.name', label: 'Cliente', type: 'text' },
    { key: 'client.email', label: 'E-mail', type: 'text' },
    { key: 'client.phone', label: 'Telefone', type: 'text' },
    { key: 'demandType', label: 'Demanda', type: 'text' },
    { key: 'healthSubtheme', label: 'Subtema', type: 'select', options: options(cards.map((card) => card.healthSubtheme ?? '')) },
    { key: 'healthObjectName', label: 'Medicamento, tratamento ou objeto', type: 'text' },
    { key: 'leadSource', label: 'Origem', type: 'select', options: options(cards.map((card) => card.leadSource ?? '')) },
    { key: 'columnId', label: 'Etapa', type: 'select', options: options(cards.map((card) => card.columnId)) },
    { key: 'microStatus', label: 'Status', type: 'select', options: options(cards.map((card) => card.microStatus)) },
    { key: 'priority', label: 'Prioridade', type: 'select', options: options(cards.map((card) => card.priority)) },
    { key: 'classification', label: 'Classificação', type: 'select', options: options(cards.map((card) => card.classification)) },
    { key: 'responsibleHuman', label: 'Responsável', type: 'select', options: options(cards.map((card) => card.responsibleHuman)) },
    { key: 'conversationInsight.closeIntent', label: 'Intenção de fechamento', type: 'select', options: options(cards.map((card) => card.conversationInsight?.closeIntent ?? '')) },
    { key: 'conversationInsight.documentReadiness', label: 'Prontidão documental', type: 'select', options: options(cards.map((card) => card.conversationInsight?.documentReadiness ?? '')) },
    { key: 'claimValue', label: 'Valor do caso', type: 'number' },
    { key: 'docScore', label: 'Score documental', type: 'number' },
    { key: 'leadScore', label: 'Score do lead', type: 'number' },
    { key: 'unreadMessages', label: 'Mensagens não lidas', type: 'number' },
    { key: 'isBlocked', label: 'Bloqueado', type: 'boolean' },
    { key: 'needsApproval', label: 'Precisa de aprovação', type: 'boolean' },
    { key: 'updatedAt', label: 'Última atualização', type: 'date' },
    { key: 'nextDeadline', label: 'Próximo prazo', type: 'date' },
  ];
}
