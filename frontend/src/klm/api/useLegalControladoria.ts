import { useQuery } from '@tanstack/react-query';
import { klmApi } from '@/klm/api';
import { isLegalDemandAccessDeniedError } from '@/klm/api/useLegalDemands';

/**
 * Hooks da Controladoria jurídica — painel diário + distribuição (Onda 4,
 * Sprint B UI). Consomem o contrato real de
 * `app/api/v1/endpoints/legal_controladoria.py` via `klmApi` (nunca
 * `fetch` cru). Ver `PrazosPage` (Publicações e Controladoria).
 */

const CONTROLADORIA_PAINEL_KEY = ['klm', 'legal', 'controladoria', 'painel'] as const;
const DISTRIBUTION_RULES_KEY = ['klm', 'legal', 'controladoria', 'distribution-rules'] as const;

export { isLegalDemandAccessDeniedError as isControladoriaAccessDeniedError };

export function useControladoriaPainel() {
  return useQuery({
    queryKey: CONTROLADORIA_PAINEL_KEY,
    queryFn: () => klmApi.controladoriaPainel(),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useDistributionRules(params: { activeOnly?: boolean } = {}) {
  return useQuery({
    queryKey: [...DISTRIBUTION_RULES_KEY, params.activeOnly ?? false],
    queryFn: () => klmApi.listDistributionRules(params),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}
