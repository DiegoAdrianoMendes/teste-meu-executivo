/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { TooltipProvider } from '@/components/ui/tooltip';
import { PermissionGate } from '@/klm/components/PermissionGate';
import { ModuleAccessDenied } from '@/klm/components/ModuleAccessDenied';
import type { Permission, WorkspaceAccessPackage } from '@/types';

// ── Mocks compartilhados ─────────────────────────────────────────────────
const requestModuleAccessMock = vi.fn().mockResolvedValue({ message: 'ok', notifiedAdmins: 1 });
vi.mock('@/services/notifications', () => ({
  requestModuleAccess: (...args: unknown[]) => requestModuleAccessMock(...args),
}));

let mockPackage: WorkspaceAccessPackage | null = null;
vi.mock('@/services/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/admin')>();
  return {
    ...actual,
    fetchMyAccessPackage: async () => mockPackage,
  };
});

let mockCan: (module: string, action?: string) => boolean = () => true;
vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ can: (m: string, a?: string) => mockCan(m, a) }),
  useOptionalAppStore: () => ({ can: (m: string, a?: string) => mockCan(m, a) }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  mockCan = () => true;
  mockPackage = null;
});

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

// ── Representação simplificada dos 4 pacotes representativos, espelhando
// app/core/workspace_access_packages.py — usada para simular `can()` por papel.
const PACKAGE_PERMISSIONS: Record<string, Permission[]> = {
  office_owner: [
    { module: 'kanban', action: 'edit', scope: 'workspace' },
    { module: 'mensagens', action: 'send_message', scope: 'workspace' },
    { module: 'automations', action: 'manage', scope: 'workspace' },
    { module: 'integrations', action: 'manage', scope: 'workspace' },
    { module: 'admin', action: 'manage', scope: 'workspace' },
  ],
  lawyer: [
    { module: 'kanban', action: 'edit', scope: 'workspace' },
    { module: 'mensagens', action: 'send_message', scope: 'workspace' },
    { module: 'lead_data', action: 'edit', scope: 'workspace' },
  ],
  secretary: [
    { module: 'kanban', action: 'view', scope: 'workspace' },
    { module: 'mensagens', action: 'view', scope: 'workspace' },
  ],
  marketing: [
    { module: 'relatorios', action: 'view', scope: 'workspace' },
    { module: 'relatorios', action: 'export', scope: 'workspace' },
  ],
};

function canFor(packageKey: string) {
  const grants = PACKAGE_PERMISSIONS[packageKey] ?? [];
  return (module: string, action = 'view') => grants.some((p) => p.module === module && p.action === action);
}

const PACKAGE_LABELS: Record<string, string> = {
  office_owner: 'Titular do escritório',
  lawyer: 'Advogado',
  secretary: 'Secretaria / recepção',
  marketing: 'Marketing / growth',
};

describe('PermissionGate', () => {
  it('variant lock: nunca remove o conteúdo, apenas desabilita e explica', async () => {
    mockCan = () => false;
    mockPackage = { key: 'secretary', label: 'Secretaria / recepção', description: '', workspaceRole: 'operator', permissionGrants: [], permissionDenials: [], permissions: [], sensitiveDataPolicy: 'view' };

    renderWithProviders(
      <PermissionGate module="kanban" action="edit">
        <button type="button">Mover card</button>
      </PermissionGate>,
    );

    const button = screen.getByRole('button', { name: 'Mover card' });
    expect(button).toBeInTheDocument();
    expect(button).toBeDisabled();
    // o grupo carrega a explicação via aria-label (tooltip acessível), assim que
    // o pacote de acesso do usuário é resolvido.
    await waitFor(() => {
      expect(screen.getByRole('group', { hidden: true })).toHaveAttribute(
        'aria-label',
        expect.stringContaining('Secretaria / recepção'),
      );
    });
  });

  it('variant readonly: mostra badge "Somente leitura" e mantém o conteúdo visível', () => {
    mockCan = () => false;
    renderWithProviders(
      <PermissionGate module="finance" action="edit" variant="readonly" showReadonlyBadge>
        <p>Bloco financeiro</p>
      </PermissionGate>,
    );
    expect(screen.getByText('Somente leitura')).toBeInTheDocument();
    expect(screen.getByText('Bloco financeiro')).toBeInTheDocument();
  });

  it('variant hide: remove o conteúdo quando não há acesso', () => {
    mockCan = () => false;
    renderWithProviders(
      <PermissionGate module="admin" action="manage" variant="hide">
        <p>Painel administrativo</p>
      </PermissionGate>,
    );
    expect(screen.queryByText('Painel administrativo')).not.toBeInTheDocument();
  });

  it('libera o conteúdo normalmente quando o pacote inclui a permissão', () => {
    mockCan = () => true;
    renderWithProviders(
      <PermissionGate module="kanban" action="edit">
        <button type="button">Mover card</button>
      </PermissionGate>,
    );
    const button = screen.getByRole('button', { name: 'Mover card' });
    expect(button).toBeInTheDocument();
    expect(button).not.toBeDisabled();
  });
});

describe('ModuleAccessDenied', () => {
  it('mostra o empty-state com CTA e notifica os administradores ao solicitar acesso', async () => {
    renderWithProviders(<ModuleAccessDenied module="kanban" />);

    expect(screen.getByText(/não faz parte do seu acesso/)).toBeInTheDocument();
    const cta = screen.getByRole('button', { name: 'Solicitar acesso' });
    fireEvent.click(cta);

    await waitFor(() => expect(requestModuleAccessMock).toHaveBeenCalledWith(
      expect.objectContaining({ module: 'kanban' }),
    ));
    await screen.findByText('Pedido enviado ao administrador');
  });
});

describe('Varredura por papel — páginas-chave x pacotes representativos', () => {
  const roles = ['office_owner', 'lawyer', 'secretary', 'marketing'] as const;

  it.each(roles)('FunnelPage renderiza sem quebrar para o pacote %s', async (roleKey) => {
    vi.resetModules();
    mockCan = canFor(roleKey);
    mockPackage = {
      key: roleKey,
      label: PACKAGE_LABELS[roleKey],
      description: '',
      workspaceRole: 'operator',
      permissionGrants: [],
      permissionDenials: [],
      permissions: PACKAGE_PERMISSIONS[roleKey],
      sensitiveDataPolicy: 'view',
    };

    const { default: FunnelPage } = await import('@/klm/pages/FunnelPage');
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <MemoryRouter initialEntries={['/app/funil']}>
            <Routes><Route path="/app/funil" element={<FunnelPage />} /></Routes>
          </MemoryRouter>
        </TooltipProvider>
      </QueryClientProvider>,
    );

    // A página não deve lançar erro de render nem ficar em branco — o cabeçalho aparece
    // para todo pacote, independentemente de conseguir editar o funil.
    expect(await screen.findByText(/Funil/)).toBeInTheDocument();
  });

  it.each(roles)('TrackingPage renderiza sem quebrar para o pacote %s', async (roleKey) => {
    mockCan = canFor(roleKey);
    mockPackage = {
      key: roleKey,
      label: PACKAGE_LABELS[roleKey],
      description: '',
      workspaceRole: 'operator',
      permissionGrants: [],
      permissionDenials: [],
      permissions: PACKAGE_PERMISSIONS[roleKey],
      sensitiveDataPolicy: 'view',
    };

    const { default: TrackingPage } = await import('@/klm/pages/TrackingPage');
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <MemoryRouter initialEntries={['/app/rastreamento']}>
            <Routes><Route path="/app/rastreamento" element={<TrackingPage />} /></Routes>
          </MemoryRouter>
        </TooltipProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('heading', { name: 'Rastreamento e aquisição' })).toBeInTheDocument();
  });
});
