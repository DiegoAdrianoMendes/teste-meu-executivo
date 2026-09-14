import { useQuery } from '@tanstack/react-query';
import { klmApi, type KlmFinanceDunningLogStatus } from '@/klm/api';

/**
 * Configuração da régua de cobrança (dunning, F4) — consome
 * GET /finance/dunning/config. Ver docs/PLANO_FINANCEIRO_PROFUNDO_2026-07-24.md §5 F4.
 */
export function useFinanceDunningConfig() {
  return useQuery({
    queryKey: ['klm', 'finance-dunning-config'],
    queryFn: klmApi.financeDunningConfig,
  });
}

export type UseFinanceDunningLogParams = {
  status?: KlmFinanceDunningLogStatus;
  limit?: number;
  enabled?: boolean;
};

/**
 * Disparos recentes da régua de cobrança no workspace, com resumo do
 * lançamento de origem — consome GET /finance/dunning/log. Passe
 * `status: 'pending_approval'` para a fila de degraus aguardando aprovação
 * humana (o D+15 formal sempre exige aprovação).
 */
export function useFinanceDunningLog(params: UseFinanceDunningLogParams = {}) {
  const { status, limit, enabled = true } = params;
  return useQuery({
    queryKey: ['klm', 'finance-dunning-log', status ?? null, limit ?? null],
    queryFn: () => klmApi.financeDunningLog({ status, limit }),
    enabled,
  });
}
