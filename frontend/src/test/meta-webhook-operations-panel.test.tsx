/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { vi } from 'vitest';

import { MetaWebhookOperationsPanel } from '@/components/whatsapp/MetaWebhookOperationsPanel';

const ALL_META_FIELDS = [
  'account_alerts', 'account_review_update', 'account_settings_update', 'account_update',
  'automatic_events', 'business_capability_update', 'business_status_update',
  'business_username_updates', 'calls', 'flows', 'group_lifecycle_update',
  'group_participants_update', 'group_settings_update', 'group_status_update', 'history',
  'message_echoes', 'message_template_components_update', 'message_template_quality_update',
  'message_template_status_update', 'messages', 'messaging_handovers', 'partner_solutions',
  'payment_configuration_update', 'phone_number_name_update', 'phone_number_quality_update',
  'security', 'smb_app_state_sync', 'smb_message_echoes', 'standby',
  'template_category_update', 'template_correct_category_detection', 'tracking_events',
  'user_preferences',
] as const;

describe('MetaWebhookOperationsPanel', () => {
  it('distingue fluxo nativo de sinal monitorado e mostra atividade real', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const data = {
      catalog: [
        {
          field: 'messages', category: 'messages', severity: 'info', implementation: 'native' as const,
          recommended: true, sprint: 2, destination: 'Conversas', description: 'Mensagens recebidas.',
          operatorAction: 'Persistir.', observationState: 'not_observed' as const, eventCount: 0,
          lastObservedAt: null, lastSummary: null,
        },
        {
          field: 'phone_number_quality_update', category: 'health', severity: 'warning',
          implementation: 'domain_projection' as const, recommended: true, sprint: 3,
          destination: 'Qualidade do número', description: 'Mudança de qualidade.',
          operatorAction: 'Alertar.', observationState: 'observed' as const, eventCount: 1,
          lastObservedAt: '2026-08-14T12:00:00Z', lastSummary: 'Qualidade alterada para amarelo',
        },
      ],
      events: [{
        id: 'event-1', workspaceId: 'workspace-1', connectionId: 'connection-1',
        field: 'phone_number_quality_update', category: 'health', severity: 'warning',
        implementation: 'domain_projection', summary: 'Qualidade alterada para amarelo',
        payload: {}, occurredAt: '2026-08-14T12:00:00Z',
      }],
      eventCount: 1,
      latestEventAt: '2026-08-14T12:00:00Z',
    };
    queryClient.setQueryData(['meta-operations-test'], data);
    const view = render(
      <QueryClientProvider client={queryClient}>
        <MetaWebhookOperationsPanel
          queryKey={['meta-operations-test']}
          queryFn={async () => data}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText('Mensagens')).toBeTruthy();
    expect(screen.getByText('Qualidade do número')).toBeTruthy();
    expect(screen.getByText('Qualidade alterada para amarelo')).toBeTruthy();
    expect(screen.getByText('Aguardando primeiro evento')).toBeTruthy();
    expect(screen.getByText('Evento recebido')).toBeTruthy();
    expect(screen.getByText(/não permite inferir se a assinatura/i)).toBeTruthy();
    view.unmount();
    queryClient.clear();
  });

  it('mantém os 33 campos visíveis mesmo quando nenhum webhook foi observado', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const data = {
      catalog: ALL_META_FIELDS.map((field, index) => ({
        field,
        category: 'operations',
        severity: 'info',
        implementation: 'domain_projection' as const,
        recommended: true,
        sprint: index < 10 ? 1 : index < 20 ? 2 : index < 30 ? 3 : 4,
        destination: `Destino ${field}`,
        description: `Descrição ${field}`,
        operatorAction: `Ação ${field}`,
        observationState: 'not_observed' as const,
        eventCount: 0,
        lastObservedAt: null,
        lastSummary: null,
      })),
      events: [],
      eventCount: 0,
      latestEventAt: null,
    };
    queryClient.setQueryData(['meta-operations-all-fields'], data);
    const view = render(
      <QueryClientProvider client={queryClient}>
        <MetaWebhookOperationsPanel
          queryKey={['meta-operations-all-fields']}
          queryFn={async () => data}
        />
      </QueryClientProvider>,
    );

    ALL_META_FIELDS.forEach((field) => expect(screen.getByText(`Destino: Destino ${field}`)).toBeTruthy());
    expect(screen.getByText('0/33 observados')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'operations' })).toBeTruthy();
    view.unmount();
    queryClient.clear();
  });

  it('exige prévia para reconciliar, usa aprovação por evento em campo crítico e permite retry limitado', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const previewSubscriptions = vi.fn(async () => ({ fieldCount: 33, connectionCount: 1 }));
    const reconcileSubscriptions = vi.fn(async () => ({ jobId: 'job-1' }));
    const retryReceipt = vi.fn(async () => ({ status: 'retrying' }));
    const updatePolicy = vi.fn(async () => ({ version: 2 }));
    const data = {
      catalog: [{
        field: 'security', category: 'security', severity: 'critical',
        implementation: 'domain_projection' as const, recommended: true, sprint: 3,
        destination: 'Segurança', description: 'Evento crítico.', operatorAction: 'Revisar.',
        observationState: 'observed' as const, eventCount: 1, lastObservedAt: '2026-08-14T12:00:00Z',
        lastSummary: 'Risco observado', desiredSubscription: true, subscriptionCount: 1,
        providerStates: ['subscribed'],
        applicability: 'waba_level', automation: {
          enabled: false, mode: 'observe_only' as const, actionKey: null, version: 1,
        },
      }],
      events: [], eventCount: 1, latestEventAt: '2026-08-14T12:00:00Z',
      receipts: [{
        id: 'receipt-1', connectionId: 'connection-1', field: 'security', status: 'failed',
        attemptCount: 1, projectionType: null, processedAt: null,
        observedAt: '2026-08-14T12:00:00Z', sanitizedErrorCode: 'HANDLER_FAILED',
        retryAvailable: true, retryManagedBy: 'receipt' as const,
      }],
      receiptCount: 1,
    };
    queryClient.setQueryData(['meta-operations-controls'], data);
    render(
      <QueryClientProvider client={queryClient}>
        <MetaWebhookOperationsPanel
          queryKey={['meta-operations-controls']}
          queryFn={async () => data}
          canManageSubscriptions
          canManagePolicies
          previewSubscriptions={previewSubscriptions}
          reconcileSubscriptions={reconcileSubscriptions}
          retryReceipt={retryReceipt}
          updatePolicy={updatePolicy}
        />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole('button', { name: /confirmar reconciliação/i })).toBeNull();
    expect(screen.getByText('1 conexão(ões)')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /gerar prévia/i }));
    await screen.findByText(/prévia pronta: 33 campos/i);
    fireEvent.click(screen.getByRole('button', { name: /confirmar reconciliação/i }));
    expect(screen.getByRole('alertdialog')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /iniciar reconciliação/i }));
    fireEvent.click(screen.getByRole('button', { name: /requer aprovação por evento/i }));
    fireEvent.click(screen.getByRole('button', { name: /tentar novamente/i }));

    await waitFor(() => expect(reconcileSubscriptions).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(retryReceipt).toHaveBeenCalledWith('receipt-1'));
    await waitFor(() => expect(updatePolicy).toHaveBeenCalledWith('security', expect.objectContaining({
      enabled: true,
      mode: 'approval_each_time',
      expectedVersion: 1,
    })));
  });
});
