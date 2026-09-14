import { describe, expect, it } from 'vitest';

import { filterMatterCards, matterCardPersonalizedFields } from '@/lib/matter-card-filters';
import { matchesPersonalizedFilter } from '@/lib/personalized-filters';
import type { Filters, MatterCard } from '@/types';

const baseFilters: Filters = {
  search: '', status: [], priority: [], urgency: [], agentId: [], columnId: [],
  classification: [], leadSource: [], closeIntent: [], documentReadiness: [],
  isBlocked: null, needsApproval: null, hasUnreadMessages: null, hasFinancialPending: null,
  docScoreBelow: null, updatedWithinDays: null, minClaimValue: null, maxClaimValue: null,
};

function card(overrides: Partial<MatterCard> = {}): MatterCard {
  return {
    id: 'matter-1',
    client: { id: 'client-1', name: 'Ana Souza', cpf: '', email: 'ana@example.com', phone: '5511999999999', employer: 'KLM', position: '' },
    practiceArea: 'plano_saude', columnId: 'qualificacao', microStatus: 'aguardando_docs', priority: 'alta', urgency: 'urgente',
    classification: 'Forte', demandType: 'Medicamento de alto custo', leadSource: 'Google Ads', healthSubtheme: 'medicamento_alto_custo',
    healthObjectName: 'Pembrolizumabe', starsRating: 4, assignedAgents: ['agent-juridico'], nextAction: 'Pedir laudo', currentAgent: 'agent-sdr', responsibleHuman: 'user-1',
    lastInteraction: '2026-07-19T12:00:00Z', unreadMessages: 2, openTasks: 1, isBlocked: false, needsApproval: true,
    docScore: 60, productionScore: 20, strategyNotes: '', risks: [], blocks: [], documents: [], tasks: [], timeline: [], conversations: [], auditLogs: [],
    claimValue: 120_000, conversationInsight: { id: 'insight-1', sourceChannel: 'whatsapp', closeIntent: 'high', documentReadiness: 'low', objections: [], requestedDocuments: [], missingInformation: [], closedSignal: false, conversionPlatforms: [], rawPayload: {}, createdAt: '2026-07-19T12:00:00Z', updatedAt: '2026-07-19T12:00:00Z' },
    createdAt: '2026-07-18T12:00:00Z', updatedAt: '2026-07-19T12:00:00Z',
    ...overrides,
  };
}

describe('matter card filters', () => {
  it('applies every standard filter to the same card collection', () => {
    const matching = card();
    const other = card({ id: 'matter-2', priority: 'baixa', leadSource: 'Indicação', unreadMessages: 0, needsApproval: false, claimValue: 2_000 });
    const filters: Filters = {
      ...baseFilters,
      search: 'pembrolizumabe', priority: ['alta'], urgency: ['urgente'], agentId: ['agent-juridico'],
      columnId: ['qualificacao'], classification: ['Forte'], leadSource: ['Google Ads'], closeIntent: ['high'],
      documentReadiness: ['low'], needsApproval: true, hasUnreadMessages: true, docScoreBelow: 70,
      updatedWithinDays: 2, minClaimValue: 100_000, maxClaimValue: 300_000,
    };

    expect(filterMatterCards([matching, other], filters, new Date('2026-07-20T12:00:00Z')).map((item) => item.id)).toEqual(['matter-1']);
  });

  it('supports saved personalized conditions over legal and commercial fields', () => {
    const cards = [card(), card({ id: 'matter-2', healthObjectName: 'Home care', claimValue: 10_000 })];
    const fields = matterCardPersonalizedFields(cards);
    const definition = {
      match: 'all' as const,
      conditions: [
        { id: 'c1', field: 'healthObjectName', operator: 'contains' as const, value: 'pembro' },
        { id: 'c2', field: 'claimValue', operator: 'gte' as const, value: 100_000 },
      ],
    };
    expect(cards.filter((item) => matchesPersonalizedFilter(item, definition, fields)).map((item) => item.id)).toEqual(['matter-1']);
  });
});
