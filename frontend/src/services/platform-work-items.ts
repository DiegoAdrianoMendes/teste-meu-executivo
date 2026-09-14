import { apiFetch } from '@/services/http';

export type PlatformWorkItemState =
  | 'open'
  | 'in_progress'
  | 'waiting_external'
  | 'waiting_customer'
  | 'blocked'
  | 'done'
  | 'cancelled'
  | 'stale';

export interface PlatformWorkItem {
  id: string;
  sourceType: string;
  sourceId: string;
  sourceVersion: string;
  kind: 'task' | 'approval' | 'alert';
  state: PlatformWorkItemState;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  workspaceId: string | null;
  assignedUserId: string | null;
  assignedTeamId: string | null;
  safeTitle: string;
  safeSummary: string;
  href: string;
  allowedCommands: string[];
  openedAt: string;
  dueAt: string | null;
  snoozedUntil: string | null;
  sourceUpdatedAt: string;
  projectedAt: string;
  projectionVersion: number;
  payloadSafe: Record<string, unknown>;
}

export interface PlatformWorkItemPage {
  items: PlatformWorkItem[];
  nextCursor: string | null;
  hasMore: boolean;
  generatedAt: string;
}

export interface PlatformWorkItemCounts {
  total: number;
  open: number;
  overdue: number;
  unassigned: number;
  byPriority: Record<string, number>;
  bySource: Record<string, number>;
  generatedAt: string;
}

export interface PlatformWorkItemAdapterResult {
  sourceType: string;
  status: 'available' | 'partial' | 'unavailable' | 'failed';
  projected: number;
  unchanged: number;
  reasonCode: string | null;
}

export interface PlatformWorkItemReconcileResult {
  adapters: PlatformWorkItemAdapterResult[];
  projectedAt: string;
}

export interface WorkItemQuery {
  scope: 'mine' | 'team' | 'platform';
  teamId?: string;
  priority?: string;
  sourceType?: string;
  search?: string;
  assignedToUserId?: string;
  unassigned?: boolean;
  overdue?: boolean;
  cursor?: string;
  limit?: number;
}

export function fetchPlatformWorkItems(query: WorkItemQuery): Promise<PlatformWorkItemPage> {
  const params = new URLSearchParams({
    scope: query.scope,
    limit: String(query.limit ?? 50),
  });
  if (query.teamId) params.set('teamId', query.teamId);
  if (query.priority && query.priority !== 'all') params.append('priorities', query.priority);
  if (query.sourceType && query.sourceType !== 'all') params.append('sourceTypes', query.sourceType);
  if (query.search?.trim()) params.set('search', query.search.trim());
  if (query.assignedToUserId) params.set('assignedToUserId', query.assignedToUserId);
  if (query.unassigned) params.set('unassigned', 'true');
  if (query.overdue) params.set('overdue', 'true');
  if (query.cursor) params.set('cursor', query.cursor);
  return apiFetch(`/api/v1/platform/work-items?${params.toString()}`);
}

export function fetchPlatformWorkItemCounts(): Promise<PlatformWorkItemCounts> {
  return apiFetch('/api/v1/platform/work-items/counts');
}

export function dispatchPlatformWorkItemCommand(
  item: PlatformWorkItem,
  command: string,
  reason: string,
): Promise<{ workItem: PlatformWorkItem }> {
  return apiFetch(`/api/v1/platform/work-items/${item.id}/commands/${command}`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `work-item:${item.id}:${item.sourceVersion}:${command}` },
    body: JSON.stringify({ expectedSourceVersion: item.sourceVersion, reason }),
  });
}

export function reconcilePlatformWorkItems(): Promise<PlatformWorkItemReconcileResult> {
  return apiFetch('/api/v1/platform/work-items/reconcile', {
    method: 'POST',
    headers: { 'Idempotency-Key': `work-item-reconcile:${new Date().toISOString().slice(0, 16)}` },
    body: JSON.stringify({ sourceTypes: null }),
  });
}
