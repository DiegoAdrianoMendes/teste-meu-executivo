/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FinanceControlPanel } from '@/components/admin/FinanceControlPanel';
import { FinanceIntegrationsPanel } from '@/components/admin/FinanceIntegrationsPanel';
import {
  FINANCIAL_UPGRADE_STATUS,
  FINANCIAL_UPGRADE_UNLOCK_TRIGGER,
  FinanceUpgradePauseNotice,
} from '@/components/finance/FinanceUpgradePauseNotice';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('financial upgrade pause', () => {
  it('shows the offline launch paths without credential controls or external navigation', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    const { container } = render(<FinanceUpgradePauseNotice showLaunchPaths />);

    expect(FINANCIAL_UPGRADE_STATUS).toBe('AGUARDANDO ATIVAÇÃO');
    expect(FINANCIAL_UPGRADE_UNLOCK_TRIGGER).toBe('destravar upgrade de funções financeiras');
    expect(screen.getByText('Upgrade financeiro nativo e avançado')).toBeInTheDocument();
    expect(screen.getByText('Asaas direto')).toBeInTheDocument();
    expect(screen.getByText('Pluga')).toBeInTheDocument();
    expect(screen.getByText('Zapier')).toBeInTheDocument();
    expect(screen.getByText(/não pede credenciais/)).toBeInTheDocument();
    // O card reflete a realidade: Asaas implementado, aguardando só a ativação.
    expect(screen.getAllByText('Implementado').length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('stores an Asaas launch plan as pending without calling an external service', async () => {
    const fetchSpy = vi.fn();
    const onSave = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('fetch', fetchSpy);

    render(
      <FinanceIntegrationsPanel
        integrations={[]}
        catalog={[{
          key: 'asaas',
          label: 'Asaas',
          category: 'financeiro',
          description: 'Cobranças planejadas',
          capabilities: ['cobranca', 'webhooks'],
          native: true,
          defaultWebhookPath: '/api/v1/integrations/webhooks/local/asaas',
          docsUrl: 'https://docs.asaas.com/',
          setupHint: {
            authType: 'api_key',
            webhookSecurity: 'token',
            requiredSettings: ['apiKey'],
            recommendedBaseUrl: 'https://api-sandbox.asaas.com/v3',
            recommendedApiVersion: 'v3',
            setupSteps: ['Configurar somente depois do desbloqueio.'],
          },
        }]}
        openApiSurface={null}
        onSave={onSave}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('Ex.: Asaas direto - sandbox futuro'), {
      target: { value: 'Asaas direto - plano local' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar plano local' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      provider: 'asaas',
      label: 'Asaas direto - plano local',
      status: 'pending',
      baseUrl: null,
      webhookUrl: null,
      externalAccountId: null,
      apiVersion: null,
    })));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('keeps creation of local finance entries enabled', async () => {
    const fetchSpy = vi.fn();
    const onSaveEntry = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('fetch', fetchSpy);

    render(
      <FinanceControlPanel
        overview={{
          totalOpenReceivables: 0,
          overdueAmount: 0,
          scheduledAmount: 0,
          paidAmount: 0,
          pendingReminders: 0,
          entriesDueSoon: 0,
          upcomingEntries: [],
          reminders: [],
          integrations: [],
          highlights: [],
        }}
        settings={{
          remindersEnabled: true,
          defaultReminderDaysBefore: 3,
          defaultReminderChannel: 'interno',
          allowClientNotification: false,
          autoCreateReceivableOnClose: true,
          openApiEnabled: false,
          externalSyncMode: 'manual',
        }}
        entries={[]}
        reminders={[]}
        onSaveSettings={vi.fn().mockResolvedValue(undefined)}
        onSaveEntry={onSaveEntry}
        onUpdateReminder={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByRole('button', { name: 'Salvar política financeira' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Título'), { target: { value: 'Honorários locais' } });
    fireEvent.change(screen.getByLabelText('Valor'), { target: { value: '1500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar lançamento' }));

    await waitFor(() => expect(onSaveEntry).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Honorários locais',
      amount: 1500,
      entryType: 'receivable',
      status: 'pending',
      currency: 'BRL',
    })));
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
