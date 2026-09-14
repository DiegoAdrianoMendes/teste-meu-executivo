import type { ApprovalItem, KanbanColumn, MatterCard, ReportMetric } from '@/types';
import { fetchMatterDetail, fetchMatters } from '@/services/matters';
import { fetchReportsSummary } from '@/services/reports';
import { apiFetch } from '@/services/http';
import { KLM_KANBAN_COLUMNS } from '@/klm/kanban-columns';

type ApprovalResponse = Record<string, any>;

function mapApproval(item: ApprovalResponse): ApprovalItem {
  return {
    id: item.id,
    cardId: item.cardId ?? item.matterId ?? item.matter_id,
    clientName: item.clientName ?? item.client_name ?? '—',
    type: item.type ?? item.approvalType ?? item.approval_type ?? 'approval',
    title: item.title,
    description: item.description,
    requestedBy: item.requestedBy ?? item.requested_by ?? '',
    requestedAt: item.requestedAt ?? item.createdAt ?? item.created_at,
    status: item.status,
    resolvedBy: item.resolvedBy ?? item.resolved_by,
    resolvedAt: item.resolvedAt ?? item.resolved_at,
    priority: item.priority,
    resolutionReason: item.resolutionReason ?? item.resolution_reason,
    documentVersionUrl: item.documentVersionUrl ?? item.document_version_url,
  };
}

export async function fetchCards(): Promise<MatterCard[]> {
  return fetchMatters();
}

export async function fetchCard(cardId: string): Promise<MatterCard> {
  return fetchMatterDetail(cardId);
}

export async function fetchColumns(): Promise<KanbanColumn[]> {
  return [...KLM_KANBAN_COLUMNS];
}

export async function fetchApprovalItems(): Promise<ApprovalItem[]> {
  const items = await apiFetch<ApprovalResponse[]>('/api/v1/approvals');
  return items.map(mapApproval);
}

export async function fetchMetrics(): Promise<ReportMetric[]> {
  const summary = await fetchReportsSummary();
  return summary.kpis;
}

export async function updateCardStatus(
  cardId: string,
  microStatus: string,
  columnId?: string
): Promise<{ success: boolean }> {
  await apiFetch(`/api/v1/matters/${cardId}/move`, {
    method: 'PUT',
    body: JSON.stringify({
      columnId: columnId ?? 'qualificacao',
      microStatus,
    }),
  });
  return { success: true };
}

export async function resolveApprovalItem(
  id: string,
  resolution: 'approved' | 'rejected' | 'changes_requested',
  resolutionReason?: string,
): Promise<{ success: boolean }> {
  await apiFetch(`/api/v1/approvals/${id}/resolve`, {
    method: 'POST',
    body: JSON.stringify({
      status: resolution,
      resolutionReason,
    }),
  });
  return { success: true };
}
