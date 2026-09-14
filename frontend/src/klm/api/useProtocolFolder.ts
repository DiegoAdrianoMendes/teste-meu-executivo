import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  klmApi,
  type KlmLegalArtifactKind,
  type KlmProtocolFolder,
  type KlmProtocolFolderBuildInput,
  type KlmProtocolReceiptRegisterInput,
} from '@/klm/api';
import { isLegalDemandAccessDeniedError } from '@/klm/api/useLegalDemands';

/**
 * Hooks da aba Protocolo (Sprint A UI, Onda 7). Consomem o contrato real
 * de `app/api/v1/endpoints/protocol_folder.py` e
 * `app/api/v1/endpoints/legal_editor.py::list_demand_artifacts` via
 * `klmApi` (nunca `fetch` cru). A plataforma PREPARA e VALIDA a pasta;
 * ela NUNCA protocola — não existe (aqui nem no backend) nenhum hook de
 * "protocolar", só `useBuildProtocolFolder` (monta/revalida) e
 * `useRegisterProtocolReceipt` (registra o recibo de um protocolo que JÁ
 * ACONTECEU fora da plataforma, sempre colado por um humano).
 */

const DEMAND_ARTIFACTS_KEY = ['klm', 'legal', 'demand-artifacts'] as const;
const PROTOCOL_FOLDER_KEY = ['klm', 'legal', 'protocol-folder'] as const;

export function useDemandLegalArtifacts(demandId: string | undefined, kind?: KlmLegalArtifactKind) {
  return useQuery({
    queryKey: [...DEMAND_ARTIFACTS_KEY, demandId, kind ?? 'todas'],
    queryFn: () => klmApi.listDemandLegalArtifacts(demandId as string, kind),
    enabled: Boolean(demandId),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

export function useProtocolFolder(demandId: string | undefined, protocolFolderId: string | undefined) {
  return useQuery({
    queryKey: [...PROTOCOL_FOLDER_KEY, demandId, protocolFolderId],
    queryFn: () => klmApi.getProtocolFolder(demandId as string, protocolFolderId as string),
    enabled: Boolean(demandId) && Boolean(protocolFolderId),
    retry: (failureCount, error) => !isLegalDemandAccessDeniedError(error) && failureCount < 2,
  });
}

/** Monta/revalida a pasta de protocolo a partir da versão aprovada do
 * artefato — recalcula TODAS as validações do §10 a cada chamada
 * (nunca promove `protocolavel` sozinho fora delas). */
export function useBuildProtocolFolder(demandId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmProtocolFolderBuildInput) => klmApi.buildProtocolFolder(demandId, payload),
    onSuccess: (folder: KlmProtocolFolder) => {
      queryClient.setQueryData([...PROTOCOL_FOLDER_KEY, demandId, folder.id], folder);
    },
  });
}

/** Registra o recibo de um protocolo que JÁ ACONTECEU fora da plataforma
 * (número/data do protocolo real, colado por um humano DEPOIS do
 * evento) — nunca dispara nem simula um protocolo. */
export function useRegisterProtocolReceipt(demandId: string, protocolFolderId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmProtocolReceiptRegisterInput) =>
      klmApi.registerProtocolReceipt(demandId, protocolFolderId as string, payload),
    onSuccess: (folder: KlmProtocolFolder) => {
      queryClient.setQueryData([...PROTOCOL_FOLDER_KEY, demandId, folder.id], folder);
    },
  });
}

/** Baixa a pasta numerada + manifesto num único `.zip` (entrega para
 * protocolo humano) e dispara o download no navegador — nunca abre uma
 * segunda coleta de arquivos, é o MESMO conteúdo já validado no último
 * `build`. */
export async function downloadProtocolFolderZip(
  demandId: string,
  protocolFolderId: string,
  filename: string,
): Promise<void> {
  const blob = await klmApi.downloadProtocolFolderZip(demandId, protocolFolderId);
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    URL.revokeObjectURL(url);
  }
}
