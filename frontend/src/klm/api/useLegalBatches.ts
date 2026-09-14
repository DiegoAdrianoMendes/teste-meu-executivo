import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  klmApi,
  type KlmLegalBatchCreateFromContextInput,
  type KlmLegalBatchItemState,
} from '@/klm/api';

/**
 * Hooks da Operação Massificada / Lotes (Onda 8, Sprint B UI — "lotes por
 * cliente"). Consomem o contrato real de
 * `app/api/v1/endpoints/legal_batches.py` via `klmApi` (nunca `fetch`
 * cru). Ver `LegalBatchPanel` (componente reusável) e os botões
 * "Processar em lote" na Demanda (`DemandaDetailPage`) e no Cliente
 * (`LeadPage`).
 */

const LEGAL_BATCH_KEY = ['klm', 'legal', 'batch'] as const;
const LEGAL_BATCH_PANEL_KEY = ['klm', 'legal', 'batch-panel'] as const;
const LEGAL_BATCH_ITEMS_KEY = ['klm', 'legal', 'batch-items'] as const;

export function useCreateLegalBatchFromContext() {
  return useMutation({
    mutationFn: (payload: KlmLegalBatchCreateFromContextInput) => klmApi.createLegalBatchFromContext(payload),
  });
}

export function useLegalBatch(batchId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_BATCH_KEY, batchId],
    queryFn: () => klmApi.getLegalBatch(batchId as string),
    enabled: Boolean(batchId),
  });
}

export function useLegalBatchPanel(batchId: string | undefined) {
  return useQuery({
    queryKey: [...LEGAL_BATCH_PANEL_KEY, batchId],
    queryFn: () => klmApi.getLegalBatchPanel(batchId as string),
    enabled: Boolean(batchId),
  });
}

export function useLegalBatchItems(batchId: string | undefined, state?: KlmLegalBatchItemState) {
  return useQuery({
    queryKey: [...LEGAL_BATCH_ITEMS_KEY, batchId, state ?? 'todos'],
    queryFn: () => klmApi.listLegalBatchItems(batchId as string, state),
    enabled: Boolean(batchId),
  });
}

function invalidateBatchQueries(queryClient: ReturnType<typeof useQueryClient>, batchId: string) {
  void queryClient.invalidateQueries({ queryKey: [...LEGAL_BATCH_KEY, batchId] });
  void queryClient.invalidateQueries({ queryKey: [...LEGAL_BATCH_PANEL_KEY, batchId] });
  void queryClient.invalidateQueries({ queryKey: [...LEGAL_BATCH_ITEMS_KEY, batchId] });
}

export function useProcessLegalBatch(batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => klmApi.processLegalBatch(batchId),
    onSuccess: () => invalidateBatchQueries(queryClient, batchId),
  });
}

export function useApproveLegalBatchItem(batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => klmApi.approveLegalBatchItem(batchId, itemId),
    onSuccess: () => invalidateBatchQueries(queryClient, batchId),
  });
}

/** "Aprovar itens elegíveis" — NUNCA cobre tese/prazo fatal/texto final/
 * protocolo; o resultado sempre traz os pulados em `skippedProtectedItemIds`. */
export function useApproveAllLegalBatchItems(batchId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => klmApi.approveAllLegalBatchItems(batchId),
    onSuccess: () => invalidateBatchQueries(queryClient, batchId),
  });
}
