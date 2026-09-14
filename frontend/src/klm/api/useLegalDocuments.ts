import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  klmApi,
  type KlmLegalDocumentAsset,
  type KlmLegalDocumentShareLinkCreateInput,
  type KlmLegalDocumentType,
} from '@/klm/api';

/**
 * Hooks do DMS jurídico — aba "Documentos" da demanda (Onda 7, Sprint A UI).
 * Consomem `app/api/v1/endpoints/legal_documents.py` via `klmApi` (nunca
 * `fetch` cru). Escopo por caso = `matterId` (o `Matter`/Kanban vinculado à
 * demanda, `KlmLegalDemand.matterId`) — o backend não guarda
 * `legal_demand_id` no `DocumentAsset`, então uma demanda sem `matterId`
 * ainda não tem DMS (ver `CaseDocumentsTab`, que mostra esse estado
 * honestamente em vez de listar documentos de outros casos).
 */

const LEGAL_DOCUMENTS_KEY = ['klm', 'legal', 'documents'] as const;
const LEGAL_DOCUMENT_QUARANTINE_KEY = ['klm', 'legal', 'documents-quarantine'] as const;
const LEGAL_DOCUMENT_SHARE_DOWNLOADS_KEY = ['klm', 'legal', 'document-share-downloads'] as const;

export function useLegalDocuments(matterId: string | null | undefined, params: { documentType?: KlmLegalDocumentType; q?: string } = {}) {
  return useQuery({
    queryKey: [...LEGAL_DOCUMENTS_KEY, matterId, params.documentType ?? 'todos', params.q ?? ''],
    queryFn: () => klmApi.listLegalDocuments({ matterId: matterId as string, ...params }),
    enabled: Boolean(matterId),
  });
}

/** Quarentena é workspace-wide no backend — filtra no cliente pelo `matterId`
 * do caso para não vazar documentos hostis de outros casos nesta aba. */
export function useCaseQuarantinedLegalDocuments(matterId: string | null | undefined) {
  return useQuery({
    queryKey: LEGAL_DOCUMENT_QUARANTINE_KEY,
    queryFn: () => klmApi.listQuarantinedLegalDocuments(),
    enabled: Boolean(matterId),
    select: (items: KlmLegalDocumentAsset[]) => items.filter((item) => item.matterId === matterId),
  });
}

export function useUploadLegalDocument(matterId: string, clientId: string | null | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => klmApi.uploadLegalDocument({ file, matterId, clientId }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LEGAL_DOCUMENTS_KEY });
      void queryClient.invalidateQueries({ queryKey: LEGAL_DOCUMENT_QUARANTINE_KEY });
    },
  });
}

export function useCreateLegalDocumentShareLink() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ assetId, payload }: { assetId: string; payload: KlmLegalDocumentShareLinkCreateInput }) =>
      klmApi.createLegalDocumentShareLink(assetId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LEGAL_DOCUMENTS_KEY });
    },
  });
}

export function useLegalDocumentShareDownloads(shareLinkId: string | null) {
  return useQuery({
    queryKey: [...LEGAL_DOCUMENT_SHARE_DOWNLOADS_KEY, shareLinkId],
    queryFn: () => klmApi.listLegalDocumentShareDownloads(shareLinkId as string),
    enabled: Boolean(shareLinkId),
  });
}

export function useRevokeLegalDocumentShareLink() {
  return useMutation({
    mutationFn: (shareLinkId: string) => klmApi.revokeLegalDocumentShareLink(shareLinkId),
  });
}
