import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { klmApi, type KlmFinanceCharge, type KlmFinanceChargeStatus } from '@/klm/api';

export type FunnelFinanceBadge = 'cobrando' | 'vencido' | 'pago';

export type FunnelMatterFinanceSummary = {
  badge: FunnelFinanceBadge | null;
  hasStructure: boolean;
  hasPendingProposal: boolean;
};

const OPEN_CHARGE_STATUSES: KlmFinanceChargeStatus[] = ['criada', 'visualizada', 'confirmada'];

/**
 * Resumo financeiro por caso para o Kanban comercial (Frente C) — badge
 * cobrando/vencido/pago, existência de estrutura financeira (arranjo ativo
 * ou lançamento) e proposta contract_billing pendente de aprovação.
 *
 * Busca em lote (4 chamadas globais reaproveitadas das mesmas chaves de
 * cache do módulo Financeiro e do Caso 360) e agrupa client-side por
 * matterId — evita N+1 por card do funil.
 */
export function useFunnelFinanceSummary(enabled = true) {
  const entriesQuery = useQuery({
    queryKey: ['klm', 'finance-entries', 'all'],
    queryFn: () => klmApi.financeEntries(),
    enabled,
  });
  const chargesQuery = useQuery({
    queryKey: ['klm', 'finance-charges'],
    queryFn: () => klmApi.listFinanceCharges({ limit: 300 }),
    enabled,
  });
  const arrangementsQuery = useQuery({
    queryKey: ['klm', 'fee-arrangements', 'all'],
    queryFn: () => klmApi.feeArrangements(),
    enabled,
  });
  const approvalsQuery = useQuery({
    queryKey: ['klm', 'approvals'],
    queryFn: klmApi.approvals,
    enabled,
  });

  const summaryByMatterId = useMemo(() => {
    const entries = entriesQuery.data ?? [];
    const charges = chargesQuery.data ?? [];
    const arrangements = arrangementsQuery.data ?? [];
    const approvals = approvalsQuery.data ?? [];

    const chargeByEntryId = new Map<string, KlmFinanceCharge>();
    for (const charge of charges) chargeByEntryId.set(charge.entryId, charge);

    const map = new Map<string, FunnelMatterFinanceSummary>();
    const ensure = (matterId: string) => {
      let current = map.get(matterId);
      if (!current) {
        current = { badge: null, hasStructure: false, hasPendingProposal: false };
        map.set(matterId, current);
      }
      return current;
    };

    for (const arrangement of arrangements) {
      if (arrangement.status === 'ativo') ensure(arrangement.matterId).hasStructure = true;
    }

    const entriesByMatterId = new Map<string, typeof entries>();
    for (const entry of entries) {
      if (!entry.matterId) continue;
      const list = entriesByMatterId.get(entry.matterId) ?? [];
      list.push(entry);
      entriesByMatterId.set(entry.matterId, list);
    }

    for (const [matterId, matterEntries] of entriesByMatterId) {
      const summary = ensure(matterId);
      summary.hasStructure = true;
      const hasOverdue = matterEntries.some((entry) => entry.status === 'overdue');
      const hasCobrando = matterEntries.some((entry) => {
        if (entry.status !== 'pending' && entry.status !== 'scheduled') return false;
        const charge = chargeByEntryId.get(entry.id);
        return charge ? OPEN_CHARGE_STATUSES.includes(charge.status) : false;
      });
      const hasPaid = matterEntries.some((entry) => entry.status === 'paid');
      summary.badge = hasOverdue ? 'vencido' : hasCobrando ? 'cobrando' : hasPaid ? 'pago' : null;
    }

    for (const approval of approvals) {
      if (approval.type === 'contract_billing_proposal' && approval.status === 'pending') {
        ensure(approval.cardId).hasPendingProposal = true;
      }
    }

    return map;
  }, [entriesQuery.data, chargesQuery.data, arrangementsQuery.data, approvalsQuery.data]);

  return {
    summaryByMatterId,
    isPending: entriesQuery.isPending || chargesQuery.isPending || arrangementsQuery.isPending || approvalsQuery.isPending,
  };
}
