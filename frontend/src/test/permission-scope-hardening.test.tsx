/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { UserEditDialog } from '@/components/admin/UserEditDialog';
import type { AdminRole, AdminUser } from '@/types';

afterEach(cleanup);

const legacyPermission = {
  module: 'integrations' as const,
  action: 'view' as const,
  scope: 'own' as const,
};

const roles: AdminRole[] = [
  {
    id: 'operator',
    name: 'Operador',
    description: 'Operação local',
    color: '#000000',
    permissions: [{ ...legacyPermission, scope: 'workspace' }],
  },
];

const user: AdminUser = {
  id: 'user-1',
  membershipId: 'membership-1',
  name: 'Operadora Local',
  initials: 'OL',
  email: 'operadora@klm.local',
  type: 'human',
  role: 'operator',
  color: '#000000',
  isActive: true,
  lastLogin: '-',
  roles: ['operator'],
  directPermissions: [legacyPermission],
  deniedPermissions: [],
  rolePermissions: roles[0].permissions,
  effectivePermissions: [],
};

describe('permission scope hardening', () => {
  it('migrates a legacy record scope to a fail-closed denial before save', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <UserEditDialog
        user={user}
        roles={roles}
        open
        onClose={() => undefined}
        onSave={onSave}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Salvar acesso' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0] as AdminUser;
    expect(saved.directPermissions).toEqual([]);
    expect(saved.deniedPermissions).toEqual([
      { module: 'integrations', action: 'view', scope: 'workspace' },
    ]);
    expect(saved.effectivePermissions).toEqual([]);
  });
});
