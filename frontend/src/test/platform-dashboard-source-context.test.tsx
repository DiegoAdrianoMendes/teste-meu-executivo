/** @vitest-environment jsdom */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  PlatformSourceContextNotice,
  resolvePlatformSourceContext,
  shouldOpenWorkspaceSourceContext,
} from '@/pages/PlatformDashboard';

describe('PlatformDashboard work-item source context', () => {
  it.each([
    ['support_ticket', '/blackbox/support-tickets', 'tickets', 'context'],
    ['support_access', '/blackbox/support-access', 'requests', 'context'],
    ['workspace_onboarding', '/blackbox/workspaces', 'onboarding', 'workspace'],
    ['billing', '/blackbox/billing', 'subscriptions', 'context'],
    ['workspace_invitation', '/blackbox/workspaces', 'invitations', 'context'],
    ['staff_invitation', '/blackbox/team', 'invitations', 'context'],
    ['publication', '/blackbox/announcements', 'publications', 'context'],
  ] as const)(
    'resolves %s only on its registered Blackbox surface',
    (sourceType, pathname, tab, focus) => {
      const params = new URLSearchParams({
        sourceType,
        sourceId: `${sourceType}-1`,
        workspaceId: 'workspace-1',
        tab,
        focus,
      });

      expect(resolvePlatformSourceContext(pathname, params)).toEqual({
        sourceType,
        sourceId: `${sourceType}-1`,
        workspaceId: 'workspace-1',
        tab,
        focus,
      });
      expect(resolvePlatformSourceContext('/blackbox/another-surface', params)).toBeNull();
    },
  );

  it('opens Conta 360 automatically only for an explicit onboarding workspace focus', () => {
    const onboarding = resolvePlatformSourceContext(
      '/blackbox/workspaces',
      new URLSearchParams({
        sourceType: 'workspace_onboarding',
        sourceId: 'milestone-1',
        workspaceId: 'workspace-1',
        tab: 'onboarding',
        focus: 'workspace',
      }),
    );
    const invitation = resolvePlatformSourceContext(
      '/blackbox/workspaces',
      new URLSearchParams({
        sourceType: 'workspace_invitation',
        sourceId: 'invitation-1',
        workspaceId: 'workspace-1',
        tab: 'invitations',
        focus: 'context',
      }),
    );

    expect(shouldOpenWorkspaceSourceContext(onboarding)).toBe(true);
    expect(shouldOpenWorkspaceSourceContext(invitation)).toBe(false);
  });

  it('states manual selection honestly and offers the related Conta 360', () => {
    const onOpenWorkspace = vi.fn();
    render(
      <PlatformSourceContextNotice
        context={{
          sourceType: 'support_ticket',
          sourceId: 'ticket-1',
          workspaceId: 'workspace-1',
          tab: 'tickets',
          focus: 'context',
        }}
        canOpenWorkspace
        onOpenWorkspace={onOpenWorkspace}
      />,
    );

    expect(screen.getByText('Contexto da Central de Tarefas')).toBeInTheDocument();
    expect(screen.getByText(/ainda não seleciona esse registro automaticamente/i))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Abrir Conta 360 relacionada' }));
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);
  });
});
