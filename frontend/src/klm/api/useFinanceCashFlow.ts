import { useQuery } from '@tanstack/react-query';
import { klmApi, type KlmFinanceCashFlowProjection } from '@/klm/api';

export type UseFinanceCashFlowParams = {
  dias?: number;
  enabled?: boolean;
};

/**
 * Fluxo de caixa projetado (entradas D+32 cartão + recorrências + saídas)
 * — consome GET /finance/reports/cash-flow.
 * Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F10.
 */
export function useFinanceCashFlow(params: UseFinanceCashFlowParams = {}) {
  const { dias = 90, enabled = true } = params;
  return useQuery<KlmFinanceCashFlowProjection>({
    queryKey: ['klm', 'finance-reports-cash-flow', dias],
    queryFn: () => klmApi.financeCashFlow({ dias }),
    enabled,
  });
}
