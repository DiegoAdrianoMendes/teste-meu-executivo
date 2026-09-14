import { useQuery } from '@tanstack/react-query';
import { klmApi, type KlmFinanceKpis } from '@/klm/api';

export type UseFinanceReportsParams = {
  startDate?: string;
  endDate?: string;
  asOf?: string;
  enabled?: boolean;
};

/**
 * KPIs financeiros do período (recebido, a receber, aging de vencidos, ticket
 * médio, inadimplência, MRR) — consome GET /finance/reports/kpis.
 * Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §4.5/F6/F10.
 */
export function useFinanceReports(params: UseFinanceReportsParams = {}) {
  const { startDate, endDate, asOf, enabled = true } = params;
  return useQuery<KlmFinanceKpis>({
    queryKey: ['klm', 'finance-reports-kpis', startDate ?? null, endDate ?? null, asOf ?? null],
    queryFn: () => klmApi.financeKpis({ startDate, endDate, asOf }),
    enabled,
  });
}
