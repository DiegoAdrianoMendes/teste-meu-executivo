import { useQuery } from '@tanstack/react-query';
import { klmApi } from '@/klm/api';
import { isLegalDemandAccessDeniedError } from '@/klm/api/useLegalDemands';

/**
 * Hooks do BI jurídico + capacidade (Onda 8, Sprint C UI). Consomem o
 * contrato real de `app/api/v1/endpoints/legal_bi.py` via `klmApi` (nunca
 * `fetch` cru). Ver `KpisPage` (abas Operação jurídica / Jurídico / IA /
 * Capacidade).
 */

export { isLegalDemandAccessDeniedError as isLegalBiAccessDeniedError };

const LEGAL_BI_OPERACAO_KEY = ['klm', 'legal', 'bi', 'operacao'] as const;
const LEGAL_BI_JURIDICO_KEY = ['klm', 'legal', 'bi', 'juridico'] as const;
const LEGAL_BI_IA_KEY = ['klm', 'legal', 'bi', 'ia'] as const;
const LEGAL_CAPACITY_KEY = ['klm', 'legal', 'capacity'] as const;

export type LegalBiPeriodParams = { periodFrom?: string; periodTo?: string };

export function useLegalBiOperacao(params: LegalBiPeriodParams = {}) {
  return useQuery({
    queryKey: [...LEGAL_BI_OPERACAO_KEY, params.periodFrom ?? null, params.periodTo ?? null],
    queryFn: () => klmApi.getLegalBiOperacao(params),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useLegalBiJuridico(params: LegalBiPeriodParams = {}) {
  return useQuery({
    queryKey: [...LEGAL_BI_JURIDICO_KEY, params.periodFrom ?? null, params.periodTo ?? null],
    queryFn: () => klmApi.getLegalBiJuridico(params),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useLegalBiIa(params: LegalBiPeriodParams = {}) {
  return useQuery({
    queryKey: [...LEGAL_BI_IA_KEY, params.periodFrom ?? null, params.periodTo ?? null],
    queryFn: () => klmApi.getLegalBiIa(params),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useLegalCapacity() {
  return useQuery({
    queryKey: LEGAL_CAPACITY_KEY,
    queryFn: () => klmApi.getLegalCapacity(),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}
