/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import { UserEditDialog } from '@/components/admin/UserEditDialog';
import { UserManagement } from '@/components/admin/UserManagement';
import type { AdminRole, AdminUser, WorkspaceAccessPackage } from '@/types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const roles: AdminRole[] = [
  {
    id: 'operator',
    name: 'Atendente',
    description: 'Atendimento comercial sem dados sensíveis por padrão.',
    color: '#111827',
    permissions: [
      { module: 'mensagens', action: 'view', scope: 'workspace' },
      { module: 'mensagens', action: 'send_message', scope: 'workspace' },
      { module: 'kanban', action: 'view', scope: 'workspace' },
    ],
  },
  {
    id: 'workspace_admin',
    name: 'Administrador',
    description: 'Administração do workspace.',
    color: '#8a5e0a',
    permissions: [
      { module: 'users', action: 'manage', scope: 'workspace' },
      { module: 'lead_data', action: 'view', scope: 'workspace' },
      { module: 'lead_data', action: 'edit', scope: 'workspace' },
    ],
  },
  {
    id: 'workspace_owner',
    name: 'Titular',
    description: 'Titular do workspace.',
    color: '#0f172a',
    permissions: [
      { module: 'dashboard', action: 'view', scope: 'workspace' },
      { module: 'users', action: 'manage', scope: 'workspace' },
    ],
  },
];

const operator: AdminUser = {
  id: 'user-1',
  membershipId: 'membership-1',
  name: 'Atendente Comercial',
  initials: 'AC',
  email: 'atendente@example.com',
  phone: '+55 11 98888-0000',
  type: 'human',
  role: 'operator',
  color: '#111827',
  isActive: true,
  lastLogin: '-',
  roles: ['operator'],
  directPermissions: [],
  deniedPermissions: [],
  rolePermissions: roles[0].permissions,
  effectivePermissions: roles[0].permissions,
  canViewSensitiveLeadData: false,
  canEditSensitiveLeadData: false,
  membershipStatus: 'active',
};

const accessPackages: WorkspaceAccessPackage[] = [
  {
    key: 'lawyer',
    label: 'Advogado',
    description: 'Atuação jurídica com dados do cliente.',
    workspaceRole: 'operator',
    permissionGrants: [
      { module: 'lead_data', action: 'view', scope: 'workspace' },
      { module: 'lead_data', action: 'edit', scope: 'workspace' },
    ],
    permissionDenials: [],
    permissions: [
      ...roles[0].permissions,
      { module: 'lead_data', action: 'view', scope: 'workspace' },
      { module: 'lead_data', action: 'edit', scope: 'workspace' },
    ],
    sensitiveDataPolicy: 'edit',
  },
  {
    key: 'secretary',
    label: 'Secretaria / recepção',
    description: 'Agenda e atendimento sem editar dados sensíveis.',
    workspaceRole: 'operator',
    permissionGrants: [
      { module: 'lead_data', action: 'view', scope: 'workspace' },
    ],
    permissionDenials: [
      { module: 'aprovacoes', action: 'view', scope: 'workspace' },
    ],
    permissions: [
      ...roles[0].permissions,
      { module: 'lead_data', action: 'view', scope: 'workspace' },
    ],
    sensitiveDataPolicy: 'view',
  },
];

describe('team member permissions', () => {
  it('lets the workspace owner edit and save an inherited permission', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const owner: AdminUser = {
      ...operator,
      id: 'owner-1',
      membershipId: 'owner-membership',
      name: 'Titular do Escritório',
      role: 'workspace_owner',
      roles: ['workspace_owner'],
      rolePermissions: roles[2].permissions,
      effectivePermissions: roles[2].permissions,
    };
    render(<UserEditDialog user={owner} roles={roles} open onClose={() => {}} onSave={onSave} />);

    const permissionsTab = screen.getByRole('tab', { name: 'Permissões' });
    fireEvent.mouseDown(permissionsTab, { button: 0 });
    fireEvent.click(permissionsTab);
    await waitFor(() => expect(permissionsTab).toHaveAttribute('data-state', 'active'));
    expect(screen.getByText('Edição das permissões do titular')).toBeInTheDocument();
    const dashboardView = screen.getByRole('checkbox', { name: /Ver em Dashboard: herdada do papel base/ });
    expect(dashboardView).toBeEnabled();
    fireEvent.click(dashboardView);
    fireEvent.click(screen.getByRole('button', { name: 'Salvar acesso' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect((onSave.mock.calls[0][0] as AdminUser).deniedPermissions).toContainEqual({
      module: 'dashboard', action: 'view', scope: 'workspace',
    });
  });

  it('creates a direct grant when the admin reveals sensitive lead data', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UserEditDialog user={operator} roles={roles} open onClose={() => {}} onSave={onSave} />);

    expect(screen.getByText('Dados sensíveis dos leads')).toBeInTheDocument();
    expect(screen.getByText('Ocultos')).toBeInTheDocument();
    const switches = screen.getAllByRole('switch');
    fireEvent.click(switches[switches.length - 1]);
    fireEvent.click(screen.getByRole('button', { name: 'Salvar acesso' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0] as AdminUser;
    expect(saved.canViewSensitiveLeadData).toBe(true);
    expect(saved.canEditSensitiveLeadData).toBe(false);
    expect(saved.directPermissions).toContainEqual({
      module: 'lead_data',
      action: 'view',
      scope: 'workspace',
    });
    expect(saved.deniedPermissions).toEqual([]);
  });

  it('removes both read and edit access when lead data is hidden', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const admin: AdminUser = {
      ...operator,
      id: 'user-admin',
      membershipId: 'membership-admin',
      name: 'Administradora',
      role: 'workspace_admin',
      roles: ['workspace_admin'],
      rolePermissions: roles[1].permissions,
      effectivePermissions: roles[1].permissions,
      canViewSensitiveLeadData: true,
      canEditSensitiveLeadData: true,
    };
    render(<UserEditDialog user={admin} roles={roles} open onClose={() => {}} onSave={onSave} />);

    expect(screen.getByText('Visíveis e editáveis')).toBeInTheDocument();
    const switches = screen.getAllByRole('switch');
    fireEvent.click(switches[switches.length - 1]);
    fireEvent.click(screen.getByRole('button', { name: 'Salvar acesso' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0] as AdminUser;
    expect(saved.canViewSensitiveLeadData).toBe(false);
    expect(saved.canEditSensitiveLeadData).toBe(false);
    expect(saved.deniedPermissions).toEqual(expect.arrayContaining([
      { module: 'lead_data', action: 'view', scope: 'workspace' },
      { module: 'lead_data', action: 'edit', scope: 'workspace' },
    ]));
  });

  it('creates an invited membership without exposing an admin password field', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<UserEditDialog user={null} roles={roles} open onClose={() => {}} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Nova Atendente' } });
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'nova@example.com' } });
    expect(screen.queryByLabelText(/senha/i)).not.toBeInTheDocument();
    expect(screen.getByText(/define a própria senha ao aceitar/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Enviar convite' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0]).toEqual(expect.objectContaining({
      name: 'Nova Atendente',
      email: 'nova@example.com',
      membershipStatus: 'invited',
    }));
  });

  it('applies a workspace function package before inviting the customer employee', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <UserEditDialog
        user={null}
        roles={roles}
        accessPackages={accessPackages}
        open
        onClose={() => {}}
        onSave={onSave}
      />,
    );

    expect(screen.getByText('Advogado')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('combobox')[0]);
    fireEvent.click(await screen.findByRole('option', { name: 'Secretaria / recepção' }));
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Secretária Cliente' } });
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'secretaria@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar convite' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0] as AdminUser;
    expect(saved.accessPackage).toBe('secretary');
    expect(saved.roles).toEqual(['operator']);
    expect(saved.canViewSensitiveLeadData).toBe(true);
    expect(saved.canEditSensitiveLeadData).toBe(false);
    expect(saved.deniedPermissions).toContainEqual({
      module: 'aprovacoes',
      action: 'view',
      scope: 'workspace',
    });
  });

  it('keeps global identity fields read-only while editing a membership', () => {
    render(<UserEditDialog user={operator} roles={roles} open onClose={() => {}} onSave={vi.fn()} />);

    expect(screen.getByLabelText('Nome')).toBeDisabled();
    expect(screen.getByLabelText('E-mail')).toBeDisabled();
    expect(screen.queryByLabelText(/senha/i)).not.toBeInTheDocument();
  });

  it('uses semantic colors instead of the inherited primary theme in access controls', () => {
    render(<UserEditDialog user={operator} roles={roles} open onClose={() => {}} onSave={vi.fn()} />);

    expect(screen.getByRole('switch', { name: 'Acesso ao workspace' })).toHaveClass('data-[state=checked]:bg-emerald-700');
    expect(screen.getByRole('switch', { name: 'Permitir acesso aos dados sensíveis dos leads' })).toHaveClass('data-[state=checked]:bg-emerald-700');
    expect(screen.getByRole('button', { name: 'Salvar acesso' })).toHaveClass('bg-[#006FE8]');
  });

  it('opens the native availability manager for an active workspace member', () => {
    const onManageAvailability = vi.fn();
    render(
      <UserManagement
        users={[operator]}
        roles={roles}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onManageAvailability={onManageAvailability}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: `Gerenciar escala de ${operator.name}` }));
    expect(onManageAvailability).toHaveBeenCalledOnce();
    expect(onManageAvailability).toHaveBeenCalledWith(operator);
  });

  it('opens access permissions by clicking the lead data status', async () => {
    render(
      <UserManagement
        users={[operator]}
        roles={roles}
        onSave={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: `Editar acesso aos dados do lead de ${operator.name}` }));
    expect(await screen.findByRole('dialog', { name: `Acesso de ${operator.name}` })).toBeInTheDocument();
  });

  it('confirms suspension, explains audit and waits for the persisted update', async () => {
    let finishSave: (() => void) | undefined;
    const onSave = vi.fn().mockImplementation(() => new Promise<void>((resolve) => { finishSave = resolve; }));
    render(<UserManagement users={[operator]} roles={roles} onSave={onSave} />);

    fireEvent.click(screen.getByRole('button', { name: `Suspender acesso de ${operator.name}` }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('logs de auditoria');
    expect(screen.getByRole('alertdialog')).toHaveTextContent('ticket à administração');
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar suspensão' }));

    expect(screen.getByRole('button', { name: 'Suspendendo...' })).toBeDisabled();
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ id: operator.id, isActive: false }));
    finishSave?.();
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  });

  it('offers resend for expired invitations without offering a copy link', () => {
    const expired: AdminUser = {
      ...operator,
      id: 'expired-user',
      membershipId: 'expired-membership',
      isActive: false,
      membershipStatus: 'invited',
      invitationId: 'invitation-2',
      invitationStatus: 'expired',
      invitationExpiresAt: '2026-07-01T12:00:00Z',
      invitationDeliveryStatus: 'sent',
    };
    const onResendInvitation = vi.fn().mockResolvedValue(undefined);
    render(
      <UserManagement
        users={[expired]}
        roles={roles}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onResendInvitation={onResendInvitation}
        onRevokeInvitation={vi.fn().mockResolvedValue(undefined)}
        onGetInvitationLink={vi.fn()}
      />,
    );

    expect(screen.getByText('Convite expirado')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copiar link de convite' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copiar link' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reenviar convite' }));
    expect(onResendInvitation).toHaveBeenCalledWith('invitation-2');
  });

  it('shows pending invitations with resend and revoke operations', async () => {
    const invited: AdminUser = {
      ...operator,
      id: 'invited-user',
      membershipId: 'invited-membership',
      isActive: false,
      membershipStatus: 'invited',
      invitationId: 'invitation-1',
      invitationStatus: 'pending',
      invitationExpiresAt: '2026-07-26T12:00:00Z',
      invitationDeliveryStatus: 'failed',
      invitationDeliveryErrorCode: 'MessageRejected',
    };
    const onResendInvitation = vi.fn().mockResolvedValue(undefined);
    const onRevokeInvitation = vi.fn().mockResolvedValue(undefined);
    const onGetInvitationLink = vi.fn().mockResolvedValue(
      '/accept-invitation/invitation-1?token=local-token',
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    render(
      <UserManagement
        users={[invited]}
        roles={roles}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onResendInvitation={onResendInvitation}
        onRevokeInvitation={onRevokeInvitation}
        onGetInvitationLink={onGetInvitationLink}
      />,
    );

    expect(screen.getByText('Convite pendente')).toBeInTheDocument();
    expect(screen.getByText(/Falha no envio por e-mail/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Copiar link de convite' }));
    await waitFor(() => expect(onGetInvitationLink).toHaveBeenCalledWith('invitation-1'));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(
      '/accept-invitation/invitation-1?token=local-token',
    ));
    expect(await screen.findByRole('status')).toHaveTextContent('Link de convite');

    fireEvent.click(screen.getByRole('button', { name: 'Reenviar convite' }));
    await waitFor(() => expect(onResendInvitation).toHaveBeenCalledWith('invitation-1'));

    fireEvent.click(screen.getByRole('button', { name: 'Revogar convite' }));
    await screen.findByText('Revogar convite pendente');
    const revokeButtons = screen.getAllByRole('button', { name: 'Revogar convite' });
    fireEvent.click(revokeButtons[revokeButtons.length - 1]);
    await waitFor(() => expect(onRevokeInvitation).toHaveBeenCalledWith('invitation-1'));
  });
});
