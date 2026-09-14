import { describe, expect, it } from 'vitest';
import { roleLabel, roleListLabel } from '@/lib/role-labels';

describe('role labels', () => {
  it('keeps backend role identifiers out of user-facing text', () => {
    expect(roleLabel('workspace_owner')).toBe('Titular do escritório');
    expect(roleLabel('workspace_admin')).toBe('Administrador do escritório');
    expect(roleListLabel(['legal_manager', 'lawyer'])).toBe('Gestor jurídico, Advogado');
  });

  it('hides an unknown backend role identifier', () => {
    expect(roleLabel('finance_team_lead')).toBe('Função personalizada');
  });
});
