/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BlackboxCommandCenter } from '@/components/blackbox/BlackboxCommandCenter';

const mocks = vi.hoisted(() => ({
  activateRootDirect: vi.fn(),
  enterSupportMode: vi.fn(),
  fetchCatalog: vi.fn(),
  search: vi.fn(),
}));

vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({
    manifest: { userId: 'platform-root-1' },
    effectiveUser: {},
  }),
}));

vi.mock('@/store/app-context', () => ({
  useOptionalAppStore: () => ({
    enterSupportMode: mocks.enterSupportMode,
  }),
}));

vi.mock('@/services/platform-search', async () => {
  const actual = await vi.importActual<typeof import('@/services/platform-search')>(
    '@/services/platform-search',
  );
  return {
    ...actual,
    executePlatformQuickCommand: vi.fn(),
    fetchPlatformCommandCatalog: mocks.fetchCatalog,
    searchPlatform: mocks.search,
  };
});

vi.mock('@/services/support-access', async () => {
  const actual = await vi.importActual<typeof import('@/services/support-access')>(
    '@/services/support-access',
  );
  return {
    ...actual,
    activatePlatformRootDirectAccess: mocks.activateRootDirect,
  };
});

vi.mock('@/support-mode/SupportStepUpPanel', () => ({
  SupportStepUpPanel: ({
    onAdministrativeSessionChange,
  }: {
    onAdministrativeSessionChange: (active: boolean) => void;
  }) => (
    <button type="button" onClick={() => onAdministrativeSessionChange(true)}>
      Concluir MFA recente
    </button>
  ),
}));

function renderCommandCenter() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <BlackboxCommandCenter />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  mocks.fetchCatalog.mockResolvedValue({
    commands: [
      {
        id: 'account.root_direct',
        label: 'Entrar diretamente',
        description: 'Abrir sessão root atribuída.',
        kind: 'mutation',
        scope: 'entity',
        reasonRequired: true,
      },
    ],
  });
  mocks.search.mockResolvedValue({
    groups: [
      {
        entityType: 'accounts',
        state: 'ready',
        items: [
          {
            id: '33333333-3333-4333-8333-333333333333',
            entityType: 'accounts',
            label: 'Conta Alpha',
            publicIdentifier: 'alpha',
            secondaryLabel: null,
            workspaceId: '33333333-3333-4333-8333-333333333333',
            workspaceLabel: 'Conta Alpha',
            status: 'active',
            maskedEmail: null,
            badges: [],
            metadata: {},
            allowedCommands: ['account.root_direct'],
            href: '/blackbox/accounts/33333333-3333-4333-8333-333333333333',
          },
        ],
      },
    ],
  });
  mocks.activateRootDirect.mockResolvedValue({
    expiresIn: 600,
    grant: { id: 'grant-root-1' },
  });
  mocks.enterSupportMode.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe('BlackboxCommandCenter root-direct step-up', () => {
  it('keeps confirmation blocked until the Super Admin completes MFA', async () => {
    renderCommandCenter();

    fireEvent.click(screen.getAllByRole('button', { name: 'Abrir busca global' })[0]);
    fireEvent.change(screen.getByRole('textbox', { name: 'Pesquisar no Blackbox' }), {
      target: { value: 'Alpha' },
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Entrar diretamente' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Motivo do comando' }), {
      target: { value: 'Diagnóstico técnico solicitado pelo titular.' },
    });

    const confirm = screen.getByRole('button', { name: 'Confirmar ação' });
    expect(confirm).toBeDisabled();
    expect(mocks.activateRootDirect).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Concluir MFA recente' }));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(mocks.activateRootDirect).toHaveBeenCalledWith(
      '33333333-3333-4333-8333-333333333333',
      'Diagnóstico técnico solicitado pelo titular.',
    ));
    expect(mocks.enterSupportMode).toHaveBeenCalledWith(
      expect.objectContaining({ expiresIn: 600 }),
      {
        id: '33333333-3333-4333-8333-333333333333',
        name: 'Conta Alpha',
        slug: 'alpha',
      },
    );
  });
});
