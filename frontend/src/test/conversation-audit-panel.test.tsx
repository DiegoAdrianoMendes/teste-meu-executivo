/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmOmnichannelAuditDashboard,
} from '@/klm/api';
import { ConversationAuditPanel } from '@/klm/components/ConversationAuditPanel';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const dashboard: KlmOmnichannelAuditDashboard = {
  findings: [{
    id: 'finding-1',
    workspaceId: 'workspace-1',
    threadId: 'thread-1',
    matterId: 'matter-1',
    messageId: 'message-1',
    findingType: 'unanswered_lead',
    severity: 'critical',
    state: 'open',
    title: 'Lead aguardando resposta',
    summary: 'O último contato do lead ainda não recebeu retorno.',
    dedupeKey: 'unanswered:thread-1:message-1',
    evidence: { waitingMinutes: 12 },
    remediation: { automaticRetryEligible: false },
    detectedAt: '2026-07-20T18:00:00Z',
    lastSeenAt: '2026-07-20T18:01:00Z',
    resolvedAt: null,
    retryCount: 0,
    maxRetries: 1,
    nextRetryAt: null,
    internalNoteMessageId: 'note-1',
    retryScheduleId: null,
    version: 2,
    createdAt: '2026-07-20T18:00:00Z',
    updatedAt: '2026-07-20T18:01:00Z',
  }],
  total: 1,
  openCount: 1,
  criticalCount: 1,
  retryScheduledCount: 0,
  waitingHumanCount: 0,
  resolvedCount: 0,
  inspectedThreads: 1,
  healthyThreads: 0,
  coveragePercent: 0,
  responseCoveragePercent: 0,
  deliveryIntegrityPercent: 100,
  generatedAt: '2026-07-20T18:02:00Z',
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConversationAuditPanel threadId="thread-1" />
    </QueryClientProvider>,
  );
}

describe('ConversationAuditPanel', () => {
  it('keeps integrity KPIs visible and supports custom filters, scans and actions', async () => {
    const auditSpy = vi.spyOn(klmApi, 'omnichannelAudit').mockResolvedValue(dashboard);
    const runSpy = vi.spyOn(klmApi, 'runOmnichannelAudit').mockResolvedValue(dashboard);
    const actionSpy = vi.spyOn(klmApi, 'actOnOmnichannelAuditFinding').mockResolvedValue({
      ...dashboard.findings[0],
      state: 'resolved',
      resolvedAt: '2026-07-20T18:03:00Z',
      version: 3,
    });

    renderPanel();

    expect(await screen.findByText('Lead aguardando resposta')).toBeInTheDocument();
    expect(screen.getAllByText('0%').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('100%')).toBeInTheDocument();
    expect(screen.getByText('1 crítico(s)')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Personalizar filtros'));
    fireEvent.change(screen.getByPlaceholderText('Filtro personalizado'), {
      target: { value: 'aguardando' },
    });
    await waitFor(() => expect(auditSpy).toHaveBeenLastCalledWith(expect.objectContaining({
      threadId: 'thread-1',
      query: 'aguardando',
    })));

    fireEvent.click(await screen.findByRole('button', { name: 'Verificar agora' }));
    await waitFor(() => expect(runSpy).toHaveBeenCalledWith({ threadId: 'thread-1', limit: 1 }));

    fireEvent.click(screen.getByRole('button', { name: 'Resolver' }));
    await waitFor(() => expect(actionSpy).toHaveBeenCalledWith('finding-1', {
      action: 'resolve',
      expectedVersion: 2,
    }));
  });

  it('refreshes only when a realtime audit event affects the current conversation', async () => {
    const auditSpy = vi.spyOn(klmApi, 'omnichannelAudit').mockResolvedValue(dashboard);
    renderPanel();
    await screen.findByText('Lead aguardando resposta');
    expect(auditSpy).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new CustomEvent('klm:realtime', {
      detail: { event: 'OMNICHANNEL_AUDIT_UPDATED', data: { thread_ids: ['thread-2'] } },
    }));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(auditSpy).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new CustomEvent('klm:realtime', {
      detail: { event: 'OMNICHANNEL_AUDIT_UPDATED', data: { thread_ids: ['thread-1'] } },
    }));
    await waitFor(() => expect(auditSpy).toHaveBeenCalledTimes(2));
  });
});
