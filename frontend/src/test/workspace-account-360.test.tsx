/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { WorkspaceAccount360 } from '@/components/platform/WorkspaceAccount360';
import { saveAdministrativeSessionToken } from '@/services/auth';
import type { WorkspaceOverviewResponse } from '@/types';

const platformApi = vi.hoisted(() => ({
  overview: vi.fn(),
  putOverrides: vi.fn(),
  grantHarmonizationBonus: vi.fn(),
  packages: vi.fn(),
  setPackage: vi.fn(),
  setBlocked: vi.fn(),
  revokeSessions: vi.fn(),
  transferOwnership: vi.fn(),
}));

const organizationApi = vi.hoisted(() => ({
  assignments: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0, hasMore: false, sort: [] }),
  milestones: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0, hasMore: false, sort: [] }),
  teams: vi.fn().mockResolvedValue([]),
  saveAssignment: vi.fn(),
  saveMilestone: vi.fn(),
}));

vi.mock('@/services/platform', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform')>('@/services/platform');
  return {
    ...actual,
    fetchWorkspaceOverview: platformApi.overview,
    putWorkspaceEntitlementOverrides: platformApi.putOverrides,
    grantWorkspaceFinanceHarmonizationBonus: platformApi.grantHarmonizationBonus,
    fetchPlatformPeopleAccessPackages: platformApi.packages,
    setPlatformPersonAccessPackage: platformApi.setPackage,
    setPlatformPersonBlocked: platformApi.setBlocked,
    revokePlatformPersonSessions: platformApi.revokeSessions,
    transferPlatformWorkspaceOwnership: platformApi.transferOwnership,
  };
});

vi.mock('@/services/platform-organization', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-organization')>('@/services/platform-organization');
  return {
    ...actual,
    fetchWorkspaceAssignmentsPage: organizationApi.assignments,
    fetchWorkspaceMilestonesPage: organizationApi.milestones,
    fetchPlatformTeamsPage: async () => {
      const items = await organizationApi.teams();
      return {
        items,
        total: items.length,
        limit: 20,
        offset: 0,
        hasMore: false,
        sort: [{ field: 'name', direction: 'asc' }],
      };
    },
    saveWorkspaceAssignment: organizationApi.saveAssignment,
    saveWorkspaceMilestone: organizationApi.saveMilestone,
  };
});

vi.mock('@/support-mode/SupportStepUpPanel', () => ({
  SupportStepUpPanel: ({
    onAdministrativeSessionChange,
  }: {
    onAdministrativeSessionChange: (active: boolean) => void;
  }) => (
    <button type="button" onClick={() => onAdministrativeSessionChange(true)}>
      Concluir step-up MFA
    </button>
  ),
}));

const baseOverview: WorkspaceOverviewResponse = {
  workspace: {
    id: 'workspace-1',
    name: 'KLM Advocacia',
    slug: 'klm',
    status: 'active',
    created_at: '2026-01-10T12:00:00Z',
  },
  subscription: {
    status: 'active',
    interval: 'monthly',
    amount_cents: 49700,
    currency: 'BRL',
    current_period_end: '2026-08-10T00:00:00Z',
    trial_end: null,
    cancel_at_period_end: false,
    plan: { id: 'plan-1', name: 'Pro', tier: 'pro' },
  },
  entitlements: [
    {
      entitlement: 'tokens',
      unit: 'tokens',
      quantitative: true,
      metered: true,
      enabled: true,
      limit_quantity: 100000,
      hard_limit: true,
      origin: 'plan',
      usage_this_month: { consumed_quantity: 25000, reserved_quantity: 0, remaining_quantity: 75000 },
    },
    {
      entitlement: 'digital_signature',
      unit: 'feature',
      quantitative: false,
      metered: false,
      enabled: false,
      limit_quantity: null,
      hard_limit: null,
      origin: 'override',
    },
  ],
  entitlement_overrides: [
    {
      entitlement: 'digital_signature',
      mode: 'revoke',
      quantity: null,
      reason: 'Cliente inadimplente',
      created_by_user_id: 'user-1',
      updated_at: '2026-07-01T12:00:00Z',
    },
  ],
  integrations: [
    { provider: 'google', label: 'Google Business', status: 'connected', last_sync_at: '2026-07-20T10:00:00Z' },
  ],
  whatsapp_numbers: [
    { display_phone_number: '+55 61 90000-0000', display_name: 'Recepção', status: 'connected', is_default: true },
  ],
  agents: {
    active_ai_agents: 2,
    published_playbooks: 3,
    autonomy_by_playbook: [{ name: 'Triagem', autonomy_mode: 'assisted' }],
  },
  members: [
    {
      user_id: 'member-1',
      name: 'Ana Advogada',
      email: 'ana@example.com',
      role: 'workspace_admin',
      status: 'active',
      access_package: 'office_admin',
      active_session_count: 2,
    },
  ],
  support: { open_tickets: 1 },
  tasks: {
    total: 8,
    open: 4,
    overdue: 1,
    blocked: 1,
    pending_review: 2,
    completed: 3,
    cancelled: 1,
    last_updated_at: '2026-07-25T14:30:00Z',
    content_included: false,
  },
  finance_kpis: null,
  recent_audit_events: [],
  private_data_included: false,
};

function renderComponent(
  props?: Partial<React.ComponentProps<typeof WorkspaceAccount360>>,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceAccount360
        workspaceId="workspace-1"
        open
        onOpenChange={() => {}}
        isPlatformRoot
        {...props}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStorage.clear();
});

function mockAccessPackages() {
  platformApi.packages.mockResolvedValue([
    { key: 'office_admin', label: 'Administrador do escritório', description: '', workspaceRole: 'workspace_admin', sensitiveDataPolicy: 'edit' },
    { key: 'finance', label: 'Financeiro', description: '', workspaceRole: 'finance_manager', sensitiveDataPolicy: 'hidden' },
  ]);
}

describe('WorkspaceAccount360', () => {
  it('lets only the root start an attributed direct-access session from Account 360', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    const onEnterDirect = vi.fn().mockResolvedValue(undefined);
    saveAdministrativeSessionToken('fresh-root-step-up');
    renderComponent({ onEnterDirect });

    fireEvent.change(await screen.findByLabelText('Motivo do acesso direto'), {
      target: { value: 'Diagnóstico do ticket SUP-1234.' },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Entrar diretamente na conta' }));

    await waitFor(() => expect(onEnterDirect).toHaveBeenCalledWith({
      id: 'workspace-1',
      name: 'KLM Advocacia',
      slug: 'klm',
    }, 'Diagnóstico do ticket SUP-1234.'));
    cleanup();
    renderComponent({ isPlatformRoot: false, onEnterDirect });
    expect(await screen.findByText('KLM Advocacia')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Entrar diretamente na conta' })).not.toBeInTheDocument();
  });

  it('opens the native MFA step-up before Account 360 allows root-direct access', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    const onEnterDirect = vi.fn().mockResolvedValue(undefined);
    renderComponent({ onEnterDirect });

    fireEvent.click(await screen.findByRole('button', { name: 'Confirmar MFA para entrar' }));
    expect(onEnterDirect).not.toHaveBeenCalled();
    expect(screen.getByText(
      'O acesso direto dispensa aprovação do cliente, mas exige confirmação MFA recente do Super Admin.',
    )).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Concluir step-up MFA' }));
    fireEvent.change(screen.getByLabelText('Motivo do acesso direto'), {
      target: { value: 'Diagnóstico de acesso após MFA.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Entrar diretamente na conta' }));

    await waitFor(() => expect(onEnterDirect).toHaveBeenCalledWith({
      id: 'workspace-1',
      name: 'KLM Advocacia',
      slug: 'klm',
    }, 'Diagnóstico de acesso após MFA.'));
  });

  it('renders operational data while keeping tenant finance and audit behind direct access', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    renderComponent();

    await waitFor(() => expect(platformApi.overview).toHaveBeenCalledWith('workspace-1'));

    expect(await screen.findByText('KLM Advocacia')).toBeInTheDocument();
    expect(screen.getByText('Pro')).toBeInTheDocument();

    // Funções (o rótulo "Tokens de IA" também aparece em Uso do mês)
    expect(screen.getAllByText('Tokens de IA').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Assinatura digital')).toBeInTheDocument();
    expect(screen.getByText(
      'Override vence o pacote. Revogar uma função esconde as telas dela para o cliente; os dados são preservados.',
    )).toBeInTheDocument();

    // Uso do mês
    expect(screen.getByText('Uso do mês')).toBeInTheDocument();
    expect(screen.getByText('25000 / 100000')).toBeInTheDocument();

    // Conexões
    expect(screen.getByText('Conexões')).toBeInTheDocument();
    expect(screen.getByText('Google Business')).toBeInTheDocument();

    // WhatsApp
    expect(screen.getByText('Recepção')).toBeInTheDocument();

    // Agentes
    expect(screen.getByText('Triagem')).toBeInTheDocument();
    expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Ana Advogada')).toBeInTheDocument();

    // Suporte
    expect(screen.getByText('Tickets abertos')).toBeInTheDocument();

    // Tarefas: apenas contagens seguras na Conta 360.
    expect(screen.getByText('Tarefas da conta')).toBeInTheDocument();
    expect(screen.getByText('8 no total')).toBeInTheDocument();
    expect(screen.getByText('Conteúdo tenant somente após entrada direta auditada')).toBeInTheDocument();
    expect(screen.queryByText('Título privado da tarefa')).not.toBeInTheDocument();

    expect(screen.queryByText('Financeiro resumido')).not.toBeInTheDocument();
    expect(screen.queryByText('Auditoria recente')).not.toBeInTheDocument();
    expect(screen.getByText('Super Admin · visão administrativa')).toBeInTheDocument();
  });

  it('requires a reason before toggling a function and sends the PUT with the override payload', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    platformApi.putOverrides.mockResolvedValue({
      overrides: [{ entitlement: 'digital_signature', mode: 'grant', quantity: null, reason: 'Liberado a pedido do cliente', createdByUserId: 'user-1', updatedAt: '2026-07-26T12:00:00Z' }],
    });
    renderComponent();

    const toggle = await screen.findByRole('switch', { name: 'Alternar Assinatura digital' });
    fireEvent.click(toggle);

    const saveButton = await screen.findByRole('button', { name: 'Salvar' });
    expect(saveButton).toBeDisabled();
    expect(platformApi.putOverrides).not.toHaveBeenCalled();

    const reasonField = screen.getByLabelText('Motivo (obrigatório)');
    fireEvent.change(reasonField, { target: { value: 'Liberado a pedido do cliente' } });
    expect(saveButton).toBeEnabled();

    fireEvent.click(saveButton);

    await waitFor(() => expect(platformApi.putOverrides).toHaveBeenCalledWith('workspace-1', [
      {
        entitlementKey: 'digital_signature',
        action: 'upsert',
        mode: 'grant',
        quantity: null,
        reason: 'Liberado a pedido do cliente',
      },
    ]));
  });

  it('requires a finite quantity before granting a stock entitlement', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue({
      ...baseOverview,
      entitlements: [
        ...baseOverview.entitlements,
        {
          entitlement: 'automations',
          unit: 'automations',
          quantitative: true,
          metered: false,
          enabled: false,
          limit_quantity: null,
          hard_limit: true,
          origin: 'not_configured',
        },
      ],
    });
    platformApi.putOverrides.mockResolvedValue({
      overrides: [
        {
          entitlement: 'automations',
          mode: 'limit',
          quantity: 5,
          reason: 'Piloto comercial com capacidade finita.',
        },
      ],
    });
    renderComponent();

    fireEvent.click(await screen.findByRole('switch', { name: 'Alternar Automações' }));

    const saveButton = screen.getByRole('button', { name: 'Salvar' });
    const reasonField = screen.getByLabelText('Motivo (obrigatório)');
    fireEvent.change(reasonField, {
      target: { value: 'Piloto comercial com capacidade finita.' },
    });
    expect(saveButton).toBeDisabled();

    const quantityField = screen.getByLabelText(
      'Quantidade (limite da capacidade, obrigatório)',
    );
    fireEvent.change(quantityField, { target: { value: '5' } });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(platformApi.putOverrides).toHaveBeenCalledWith('workspace-1', [
        {
          entitlementKey: 'automations',
          action: 'upsert',
          mode: 'limit',
          quantity: 5,
          reason: 'Piloto comercial com capacidade finita.',
        },
      ]),
    );
  });

  it('re-limits an active quantitative entitlement without disabling it first', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    platformApi.putOverrides.mockResolvedValue({
      overrides: [
        {
          entitlement: 'tokens',
          mode: 'limit',
          quantity: 200000,
          reason: 'Ajuste negociado da franquia mensal.',
        },
      ],
    });
    renderComponent();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Alterar limite de Tokens de IA' }),
    );

    const quantityField = screen.getByLabelText(
      'Quantidade (limite mensal, obrigatório)',
    );
    expect(quantityField).toHaveValue(100000);
    fireEvent.change(quantityField, { target: { value: '200000' } });
    fireEvent.change(screen.getByLabelText('Motivo (obrigatório)'), {
      target: { value: 'Ajuste negociado da franquia mensal.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() =>
      expect(platformApi.putOverrides).toHaveBeenCalledWith('workspace-1', [
        {
          entitlementKey: 'tokens',
          action: 'upsert',
          mode: 'limit',
          quantity: 200000,
          reason: 'Ajuste negociado da franquia mensal.',
        },
      ]),
    );
  });

  it('uses the canonical metered flag even when monthly usage is unavailable', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue({
      ...baseOverview,
      entitlements: [
        ...baseOverview.entitlements,
        {
          entitlement: 'ai_conversations',
          unit: 'conversations',
          quantitative: true,
          metered: true,
          enabled: false,
          limit_quantity: null,
          hard_limit: true,
          origin: 'not_configured',
          error: 'Contrato não configurado.',
        },
      ],
    });
    renderComponent();

    fireEvent.click(
      await screen.findByRole('switch', { name: 'Alternar Conversas de IA' }),
    );

    expect(
      screen.getByLabelText('Quantidade (limite mensal, obrigatório)'),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Conversas de IA')).toHaveLength(1);
  });

  it('shows operational metadata but hides private finance and mutations for non-root staff', async () => {
    platformApi.overview.mockResolvedValue({
      ...baseOverview,
      finance_kpis: null,
      recent_audit_events: [],
      private_data_included: false,
    });
    renderComponent({ isPlatformRoot: false });

    expect(await screen.findByText('KLM Advocacia')).toBeInTheDocument();
    expect(screen.getByText('Leitura operacional · dados privados ocultos')).toBeInTheDocument();
    expect(screen.getByText('Google Business')).toBeInTheDocument();
    expect(screen.getByText('Ana Advogada')).toBeInTheDocument();
    expect(screen.queryByText('Financeiro resumido')).not.toBeInTheDocument();
    expect(screen.queryByText('Auditoria recente')).not.toBeInTheDocument();
    expect(screen.queryByText('Bônus de harmonização financeira (F14)')).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Alternar Assinatura digital' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Alterar limite de Tokens de IA' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Tarefas da conta')).toBeInTheDocument();
    expect(screen.getByText('Conteúdo tenant exige acesso assistido aprovado')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Entrar e abrir tarefas' })).not.toBeInTheDocument();
  });

  it('grants a finance harmonization quota bonus (F14, root only) and shows the resulting quota', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    platformApi.grantHarmonizationBonus.mockResolvedValue({
      competence: '2026-07',
      used: 3,
      limit: 8,
      remaining: 5,
      renewsAt: '2026-08-01',
    });
    renderComponent();

    expect(await screen.findByText('Bônus de harmonização financeira (F14)')).toBeInTheDocument();

    const competenceField = screen.getByLabelText('Competência (AAAA-MM)');
    fireEvent.change(competenceField, { target: { value: '2026-07' } });
    const extraField = screen.getByLabelText('Verificações extras');
    fireEvent.change(extraField, { target: { value: '5' } });
    const reasonField = screen.getByLabelText('Motivo da concessão');
    fireEvent.change(reasonField, {
      target: { value: 'Compensação comercial aprovada para a conta.' },
    });

    const grantButton = screen.getByRole('button', { name: 'Conceder bônus' });
    expect(grantButton).toBeEnabled();
    fireEvent.click(grantButton);

    await waitFor(() =>
      expect(platformApi.grantHarmonizationBonus).toHaveBeenCalledWith('workspace-1', {
        competence: '2026-07',
        extra: 5,
        reason: 'Compensação comercial aprovada para a conta.',
      }),
    );
    expect(await screen.findByText(/3\/8 usadas · 5 restantes/)).toBeInTheDocument();
  });

  it('keeps the harmonization bonus button disabled without a valid extra quantity', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    renderComponent();

    await screen.findByText('Bônus de harmonização financeira (F14)');
    const grantButton = screen.getByRole('button', { name: 'Conceder bônus' });
    expect(grantButton).toBeDisabled();
    expect(platformApi.grantHarmonizationBonus).not.toHaveBeenCalled();
  });

  it('lets root suspend a member and revoke sessions from Account 360 with confirmation', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    platformApi.setBlocked.mockResolvedValue({ membership_status: 'suspended' });
    platformApi.revokeSessions.mockResolvedValue({ user_id: 'member-1', workspace_id: 'workspace-1', revoked_count: 2 });
    renderComponent();

    fireEvent.click(await screen.findByRole('button', { name: 'Encerrar sessões' }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('Todos os dispositivos');
    const sessionReason = 'Revogação preventiva solicitada no ticket SUP-200.';
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), { target: { value: sessionReason } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(platformApi.revokeSessions).toHaveBeenCalledWith('workspace-1', 'member-1', sessionReason));

    fireEvent.click(screen.getByRole('button', { name: 'Suspender' }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('O vínculo será suspenso');
    const blockReason = 'Suspensão administrativa confirmada no ticket SUP-201.';
    fireEvent.change(screen.getByLabelText('Motivo da alteração'), { target: { value: blockReason } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(platformApi.setBlocked).toHaveBeenCalledWith('workspace-1', 'member-1', true, blockReason));
  });

  it('does not expose member mutation controls to non-root staff', async () => {
    platformApi.overview.mockResolvedValue({ ...baseOverview, finance_kpis: null, recent_audit_events: [], private_data_included: false });
    renderComponent({ isPlatformRoot: false });
    await screen.findByText('Ana Advogada');
    expect(screen.queryByRole('button', { name: 'Encerrar sessões' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Suspender' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Pacote de Ana Advogada')).not.toBeInTheDocument();
  });

  it('transfers account ownership only after a reason is supplied', async () => {
    mockAccessPackages();
    platformApi.overview.mockResolvedValue(baseOverview);
    platformApi.transferOwnership.mockResolvedValue({
      workspace_id: 'workspace-1', owner_user_id: 'member-1', demoted_user_ids: ['old-owner'], revoked_sessions: 2,
    });
    renderComponent();

    fireEvent.click(await screen.findByRole('button', { name: 'Tornar titular' }));
    const confirm = screen.getByRole('button', { name: 'Confirmar' });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Motivo da transferência'), { target: { value: 'Solicitação formal SUP-100' } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(platformApi.transferOwnership).toHaveBeenCalledWith(
      'workspace-1',
      'member-1',
      'Solicitação formal SUP-100',
    ));
  });
});
