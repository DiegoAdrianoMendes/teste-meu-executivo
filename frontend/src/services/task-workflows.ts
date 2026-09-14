import { apiFetch } from '@/services/http';

export interface TaskWorkflowRun {
  id: string;
  flowId: string;
  revisionId: string;
  matterId: string;
  taskId: string;
  flowName: string;
  status: string;
  currentNodeId: string | null;
  pauseReason: string | null;
  errorMessage: string | null;
  startedAt: string;
  completedAt: string | null;
  pausedAt: string | null;
}

export function listTaskWorkflowRuns(taskId: string) {
  return apiFetch<TaskWorkflowRun[]>(`/api/v1/tasks/${encodeURIComponent(taskId)}/workflow-runs`);
}

export function applyTaskWorkflow(taskId: string, flowId: string, idempotencyKey: string) {
  return apiFetch<TaskWorkflowRun>(`/api/v1/tasks/${encodeURIComponent(taskId)}/workflow-runs`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ flowId }),
  });
}
