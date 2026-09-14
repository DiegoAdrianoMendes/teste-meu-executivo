import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/services/http';
import {
  klmApi,
  type KlmCaseFactCreateInput,
  type KlmCasePartyCreateInput,
  type KlmCasePleadingCreateInput,
  type KlmCaseProofLinkCreateInput,
  type KlmLegalDemandCreateInput,
  type KlmLegalDemandNature,
  type KlmLegalDemandPhase,
  type KlmLegalDemandPhaseTransitionInput,
  type KlmLegalDemandUpdateInput,
} from '@/klm/api';

/**
 * Hooks da Cadeia Jurídica — Demandas (Onda 2/5, Sprint A UI). Consomem o
 * contrato real de `app/api/v1/endpoints/legal_demands.py` e
 * `app/api/v1/endpoints/legal_case_strategy.py` via `klmApi` (nunca `fetch`
 * cru). Ver `DemandasPage` e `DemandaDetailPage`.
 */

/** `require_permission("legal", ...)` recusa com 403 quando o usuário não
 * tem a permissão do módulo jurídico no papel atual — nunca 200 vazio. As
 * páginas usam este helper para mostrar o estado honesto em vez de uma
 * lista vazia silenciosa. */
export function isLegalDemandAccessDeniedError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 402 || error.status === 403);
}

const LEGAL_DEMANDS_KEY = ['klm', 'legal', 'demands'] as const;
const LEGAL_DEMAND_DETAIL_KEY = ['klm', 'legal', 'demand'] as const;
const LEGAL_CASE_360_KEY = ['klm', 'legal', 'case-360'] as const;
const LEGAL_CASE_PARTIES_KEY = ['klm', 'legal', 'case-parties'] as const;
const LEGAL_CASE_STRATEGY_KEY = ['klm', 'legal', 'case-strategy'] as const;

export function useLegalDemands(
  params: { nature?: KlmLegalDemandNature; phase?: KlmLegalDemandPhase } = {},
) {
  return useQuery({
    queryKey: [...LEGAL_DEMANDS_KEY, params.nature ?? 'todas', params.phase ?? 'todas'],
    queryFn: () => klmApi.listLegalDemands(params),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useLegalDemand(demandId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_DEMAND_DETAIL_KEY, demandId],
    queryFn: () => klmApi.getLegalDemand(demandId as string),
    enabled: Boolean(demandId),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useCreateLegalDemand() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmLegalDemandCreateInput) => klmApi.createLegalDemand(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LEGAL_DEMANDS_KEY });
    },
  });
}

export function useUpdateLegalDemand(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmLegalDemandUpdateInput) => klmApi.updateLegalDemand(demandId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LEGAL_DEMANDS_KEY });
      void queryClient.invalidateQueries({ queryKey: [...LEGAL_DEMAND_DETAIL_KEY, demandId] });
      void queryClient.invalidateQueries({ queryKey: [...LEGAL_CASE_360_KEY, demandId] });
    },
  });
}

export function useTransitionLegalDemandPhase(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmLegalDemandPhaseTransitionInput) =>
      klmApi.transitionLegalDemandPhase(demandId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LEGAL_DEMANDS_KEY });
      void queryClient.invalidateQueries({ queryKey: [...LEGAL_DEMAND_DETAIL_KEY, demandId] });
      void queryClient.invalidateQueries({ queryKey: [...LEGAL_CASE_360_KEY, demandId] });
    },
  });
}

export function useCase360(demandId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_CASE_360_KEY, demandId],
    queryFn: () => klmApi.getLegalDemandCase360(demandId as string),
    enabled: Boolean(demandId),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useCaseParties(demandId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_CASE_PARTIES_KEY, demandId],
    queryFn: () => klmApi.listCaseParties(demandId as string),
    enabled: Boolean(demandId),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useAddCaseParty(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmCasePartyCreateInput) => klmApi.addCaseParty(demandId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [...LEGAL_CASE_PARTIES_KEY, demandId] });
      void queryClient.invalidateQueries({ queryKey: [...LEGAL_CASE_360_KEY, demandId] });
    },
  });
}

export function useCaseStrategyMatrix(demandId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_CASE_STRATEGY_KEY, demandId],
    queryFn: () => klmApi.getCaseStrategyMatrix(demandId as string),
    enabled: Boolean(demandId),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

function invalidateStrategyQueries(queryClient: ReturnType<typeof useQueryClient>, demandId: string) {
  void queryClient.invalidateQueries({ queryKey: [...LEGAL_CASE_STRATEGY_KEY, demandId] });
  void queryClient.invalidateQueries({ queryKey: [...LEGAL_CASE_360_KEY, demandId] });
}

export function useAddCaseFact(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmCaseFactCreateInput) => klmApi.addCaseFact(demandId, payload),
    onSuccess: () => invalidateStrategyQueries(queryClient, demandId),
  });
}

export function useAddCaseProofLink(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ factId, payload }: { factId: string; payload: KlmCaseProofLinkCreateInput }) =>
      klmApi.addCaseProofLink(demandId, factId, payload),
    onSuccess: () => invalidateStrategyQueries(queryClient, demandId),
  });
}

export function useAddCasePleading(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmCasePleadingCreateInput) => klmApi.addCasePleading(demandId, payload),
    onSuccess: () => invalidateStrategyQueries(queryClient, demandId),
  });
}
