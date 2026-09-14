import { apiFetch } from '@/services/http';
import type {
  KlmFinanceCostCenter,
  KlmFinanceLookupPage,
  KlmFinanceLookupType,
  KlmFunnel,
  KlmThread,
} from '@/klm/api';

/**
 * Focused lookup client for the flow node editor.
 *
 * Keeping these reads outside the broad `klmApi` runtime object prevents the
 * complete KLM API catalogue from being pulled into the node configuration
 * chunk. Type-only imports are erased by the compiler.
 */
export const flowNodeLookups = {
  funnel: () => apiFetch<KlmFunnel>('/api/v1/klm/funnel'),
  threads: () => apiFetch<KlmThread[]>('/api/v1/klm/omnichannel/threads'),
  financeCostCenters: () =>
    apiFetch<KlmFinanceCostCenter[]>('/api/v1/finance/dimensional/cost-centers'),
  finance: (
    entityType: KlmFinanceLookupType,
    options: { search?: string; cursor?: string | null; limit?: number } = {},
  ) => {
    const query = new URLSearchParams({
      entity_type: entityType,
      search: options.search ?? '',
      limit: String(options.limit ?? 50),
    });
    if (options.cursor) query.set('cursor', options.cursor);
    return apiFetch<KlmFinanceLookupPage>(
      `/api/v1/finance/dimensional/lookups?${query.toString()}`,
    );
  },
};
