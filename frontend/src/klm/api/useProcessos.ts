import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/services/http';
import {
  klmApi,
  type KlmDeadlineStatus,
  type KlmMonitoredProcessCreateInput,
  type KlmMonitoredProcessStatus,
  type KlmMonitoredProcessUpdateInput,
} from '@/klm/api';

/**
 * Hooks do módulo processual contratável à parte (add-on) — consomem o
 * contrato real de `app/api/v1/endpoints/process_monitoring.py` via
 * `klmApi` (nunca `fetch` cru). Ver `ProcessosPage` e `PrazosPage`.
 */

/** O backend recusa TODA a superfície do módulo (inclusive leitura) com
 * 402/403 quando o add-on não está contratado — nunca 200 vazio. As
 * páginas usam este helper para decidir entre a listagem real e o card
 * honesto "módulo não contratado". */
export function isModuleNotContractedError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 402 || error.status === 403);
}

const MONITORED_PROCESSES_KEY = ['klm', 'processos', 'monitorados'] as const;
const PROCESS_PUBLICATIONS_KEY = ['klm', 'processos', 'publicacoes'] as const;
const PROCESS_DEADLINES_KEY = ['klm', 'processos', 'prazos'] as const;

export function useMonitoredProcesses(status?: KlmMonitoredProcessStatus) {
  return useQuery({
    queryKey: [...MONITORED_PROCESSES_KEY, status ?? 'todos'],
    queryFn: () => klmApi.listMonitoredProcesses(status ? { status } : {}),
    retry: (failureCount, error) => !isModuleNotContractedError(error) && failureCount < 2,
  });
}

export function useCreateMonitoredProcess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: KlmMonitoredProcessCreateInput) => klmApi.createMonitoredProcess(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: MONITORED_PROCESSES_KEY });
    },
  });
}

export function useUpdateMonitoredProcess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ processId, payload }: { processId: string; payload: KlmMonitoredProcessUpdateInput }) =>
      klmApi.updateMonitoredProcess(processId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: MONITORED_PROCESSES_KEY });
    },
  });
}

export function usePauseMonitoredProcess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (processId: string) => klmApi.pauseMonitoredProcess(processId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: MONITORED_PROCESSES_KEY });
    },
  });
}

export function useResumeMonitoredProcess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (processId: string) => klmApi.resumeMonitoredProcess(processId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: MONITORED_PROCESSES_KEY });
    },
  });
}

export function useProcessPublications(params: { urgencia?: string; prazoStatus?: KlmDeadlineStatus } = {}) {
  return useQuery({
    queryKey: [...PROCESS_PUBLICATIONS_KEY, params.urgencia ?? 'todas', params.prazoStatus ?? 'todos'],
    queryFn: () => klmApi.listProcessPublications(params),
    retry: (failureCount, error) => !isModuleNotContractedError(error) && failureCount < 2,
  });
}

/** Agenda de prazos (`GET /processos/prazos`) — usada pela PrazosPage. */
export function useProcessDeadlines(status?: KlmDeadlineStatus) {
  return useQuery({
    queryKey: [...PROCESS_DEADLINES_KEY, status ?? 'todos'],
    queryFn: () => klmApi.listProcessDeadlines(status ? { status } : {}),
    retry: (failureCount, error) => !isModuleNotContractedError(error) && failureCount < 2,
  });
}

function invalidateDeadlineQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: PROCESS_PUBLICATIONS_KEY });
  void queryClient.invalidateQueries({ queryKey: PROCESS_DEADLINES_KEY });
}

export function useConfirmProcessDeadline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (publicationId: string) => klmApi.confirmProcessPublicationDeadline(publicationId),
    onSuccess: () => invalidateDeadlineQueries(queryClient),
  });
}

export function useDismissProcessDeadline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (publicationId: string) => klmApi.dismissProcessPublicationDeadline(publicationId),
    onSuccess: () => invalidateDeadlineQueries(queryClient),
  });
}
