/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  user: {
    id: 'staff-1',
    isPlatformRoot: false,
    platformAccessActive: true,
    platformPermissions: [
      { module: 'ai_operations', action: 'view' },
      { module: 'ai_operations', action: 'manage' },
    ],
  },
  session: null as null | {
    grantId: string;
    workspaceId: string;
    workspaceName: string;
    scopes: Array<'configurations:read' | 'configurations:write'>;
    expiresAt: string;
    sessionType: 'support_approved';
  },
}));

vi.mock('@/blackbox/session-manifest', () => ({
  useBlackboxManifest: () => ({ effectiveUser: state.user }),
}));

vi.mock('@/support-mode/session', () => ({
  getActiveSupportMode: () => state.session,
  subscribeToSupportMode: () => () => undefined,
}));

vi.mock('@/klm/components/AgentPlatformOperationsPanel', () => ({
  AgentPlatformOperationsPanel: ({ workspaceId, canManage, isPlatformRoot }: {
    workspaceId: string | null;
    canManage: boolean;
    isPlatformRoot: boolean;
  }) => <output data-testid="agent-platform-panel">{JSON.stringify({ workspaceId, canManage, isPlatformRoot })}</output>,
}));

vi.mock('@/components/platform/StudioWorkspaceCombobox', () => ({
  StudioWorkspaceCombobox: ({ onChange }: { onChange: (workspace: { id: string; name: string }) => void }) => (
    <button type="button" onClick={() => onChange({ id: 'root-workspace', name: 'Conta Root' })}>Selecionar conta root</button>
  ),
}));

import BlackboxAgentOperations from '@/pages/BlackboxAgentOperations';

function renderPage() {
  return render(<MemoryRouter><BlackboxAgentOperations /></MemoryRouter>);
}

afterEach(() => {
  cleanup();
  state.user = {
    id: 'staff-1',
    isPlatformRoot: false,
    platformAccessActive: true,
    platformPermissions: [
      { module: 'ai_operations', action: 'view' },
      { module: 'ai_operations', action: 'manage' },
    ],
  };
  state.session = null;
});

describe('BlackboxAgentOperations support-session gate', () => {
  it('fails closed for a non-root operator without an approved configuration session', () => {
    renderPage();

    expect(screen.getByTestId('blackbox-agent-operations-restricted')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Solicitar ou acompanhar acesso aprovado/ })).toHaveAttribute(
      'href',
      '/blackbox/support-access',
    );
    expect(screen.queryByTestId('agent-platform-panel')).not.toBeInTheDocument();
  });

  it('locks a non-root operator to the approved support workspace and preserves manage only with write scope', () => {
    state.session = {
      grantId: 'grant-1',
      workspaceId: 'workspace-authorized',
      workspaceName: 'Conta autorizada',
      scopes: ['configurations:read', 'configurations:write'],
      expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
      sessionType: 'support_approved',
    };
    renderPage();

    expect(screen.getByTestId('blackbox-agent-support-workspace')).toHaveTextContent('Conta autorizada');
    expect(screen.queryByRole('button', { name: 'Selecionar conta root' })).not.toBeInTheDocument();
    expect(screen.getByTestId('agent-platform-panel')).toHaveTextContent('workspace-authorized');
    expect(screen.getByTestId('agent-platform-panel')).toHaveTextContent('"canManage":true');
    expect(screen.getByRole('link', { name: 'Abrir Conta 360' })).toHaveAttribute(
      'href',
      '/blackbox/workspaces?workspaceId=workspace-authorized',
    );
  });

  it('keeps root workspace selection unrestricted', () => {
    state.user = {
      id: 'root-1',
      isPlatformRoot: true,
      platformAccessActive: true,
      platformPermissions: [],
    };
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: 'Selecionar conta root' }));
    expect(screen.getByTestId('agent-platform-panel')).toHaveTextContent('root-workspace');
    expect(screen.getByTestId('agent-platform-panel')).toHaveTextContent('"isPlatformRoot":true');
  });
});
