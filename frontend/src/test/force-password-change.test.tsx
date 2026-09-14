/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import ForcePasswordChange from '@/pages/ForcePasswordChange';
import { requiresPasswordChange } from '@/services/auth';

const { logout, apiFetch, appState } = vi.hoisted(() => ({
  logout: vi.fn(),
  apiFetch: vi.fn(),
  appState: {
    currentUser: {
      id: 'user-1',
      name: 'Ana',
      email: 'ana@example.com',
      role: 'lawyer',
      initials: 'A',
      requiresPasswordSetup: true,
    },
    session: { surface: 'workspace' },
  },
}));

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    state: appState,
    logout,
  }),
}));

vi.mock('@/components/admin/AuthSecurityPanel', () => ({
  AuthSecurityPanel: ({ onMfaEnabled }: { onMfaEnabled?: () => void }) => (
    <button type="button" onClick={onMfaEnabled}>Simular MFA habilitado</button>
  ),
}));

vi.mock('@/services/http', () => ({
  apiFetch,
  ApiError: class ApiError extends Error {},
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  appState.session.surface = 'workspace';
});

describe('first-access password rotation', () => {
  it('recognizes all supported backend flag names and treats missing flags as false', () => {
    expect(requiresPasswordChange({ requiresPasswordSetup: true }, null)).toBe(true);
    expect(requiresPasswordChange({ requiresPasswordChange: true }, null)).toBe(true);
    expect(requiresPasswordChange({ mustChangePassword: true }, null)).toBe(true);
    expect(requiresPasswordChange({}, {})).toBe(false);
  });

  it('requires the current password, validates the policy, and submits the change', async () => {
    apiFetch.mockResolvedValue({ message: 'Senha alterada.' });
    render(<ForcePasswordChange />);

    fireEvent.change(screen.getByLabelText('Senha provisória atual'), { target: { value: 'Provisoria10' } });
    fireEvent.change(screen.getByLabelText('Nova senha pessoal'), { target: { value: 'NovaSenha10' } });
    fireEvent.change(screen.getByLabelText('Confirmar nova senha'), { target: { value: 'NovaSenha10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Alterar senha e continuar' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Senha alterada com sucesso.');
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/auth/password', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ currentPassword: 'Provisoria10', newPassword: 'NovaSenha10' }),
    }));
    expect(screen.getByRole('button', { name: 'Entrar novamente' })).toBeInTheDocument();
  });

  it('does not call the API when confirmation differs', () => {
    render(<ForcePasswordChange />);
    fireEvent.change(screen.getByLabelText('Senha provisória atual'), { target: { value: 'Provisoria10' } });
    fireEvent.change(screen.getByLabelText('Nova senha pessoal'), { target: { value: 'NovaSenha10' } });
    fireEvent.change(screen.getByLabelText('Confirmar nova senha'), { target: { value: 'OutraSenha10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Alterar senha e continuar' }));

    expect(screen.getByRole('alert')).toHaveTextContent('não coincidem');
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('requires MFA enrollment before rotating a provisional Blackbox password', () => {
    appState.session.surface = 'platform';
    render(<ForcePasswordChange />);

    expect(screen.getByText('Primeiro, proteja o acesso à Blackbox.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Senha provisória atual')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Simular MFA habilitado' }));

    expect(screen.getByLabelText('Senha provisória atual')).toBeInTheDocument();
  });
});
