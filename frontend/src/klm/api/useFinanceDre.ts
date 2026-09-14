import { useQuery } from '@tanstack/react-query';
import { klmApi, type KlmFinanceDre } from '@/klm/api';

export type UseFinanceDreParams = {
  competencia?: string;
  enabled?: boolean;
};

/**
 * DRE gerencial (receita − despesa por categoria, detalhe mensal 12 meses)
 * — consome GET /finance/reports/dre.
 * Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F10.
 */
export function useFinanceDre(params: UseFinanceDreParams = {}) {
  const { competencia, enabled = true } = params;
  return useQuery<KlmFinanceDre>({
    queryKey: ['klm', 'finance-reports-dre', competencia ?? null],
    queryFn: () => klmApi.financeDre({ competencia }),
    enabled,
  });
}
