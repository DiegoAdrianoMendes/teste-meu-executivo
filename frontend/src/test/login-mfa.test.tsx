/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import Login from '@/pages/Login';

const appStore = vi.hoisted(() => ({ login: vi.fn() }));
const auth = vi.hoisted(() => ({
  consumeSessionExpiredNotice: vi.fn((): string | null => null),
  extractRefreshToken: vi.fn(() => ''),
  extractToken: vi.fn((data: { accessToken?: string }) => data.accessToken ?? ''),
  login: vi.fn(),
  saveRefreshToken: vi.fn(),
  saveSession: vi.fn(),
  saveToken: vi.fn(),
  saveUser: vi.fn(),
  verify: vi.fn(),
}));

vi.mock('@/store/app-context', () => ({ useAppStore: () => appStore }));
vi.mock('@/services/auth', () => ({
  consumeSessionExpiredNotice: auth.consumeSessionExpiredNotice,
  extractRefreshToken: auth.extractRefreshToken,
  extractToken: auth.extractToken,
  isMfaLoginChallenge: (value: { requiresMfa?: boolean }) => value.requiresMfa === true,
  loginWithCredentials: auth.login,
  saveRefreshToken: auth.saveRefreshToken,
  saveSession: auth.saveSession,
  saveToken: auth.saveToken,
  saveUser: auth.saveUser,
  verifyMfaLogin: auth.verify,
}));

const authenticated = {
  accessToken: 'mfa-access-token',
  refreshToken: null,
  user: {
    id: 'user-1',
    name: 'Admin KLM',
    email: 'admin@klm.test',
    role: 'admin',
    initials: 'AK',
    isInternalStaff: true,
    isPlatformAdmin: false,
  },
  session: {
    activeWorkspace: { id: 'workspace-1', name: 'KLM', slug: 'klm' },
    availableWorkspaces: [],
    permissions: [],
    isInternalStaff: true,
    activeRole: null,
  },
};

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/app" element={<div>Aplicação autenticada</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Login com MFA', () => {
  it('exibe de forma persistente que a sessão expirou após o redirecionamento global', () => {
    auth.consumeSessionExpiredNotice.mockReturnValueOnce('Sua sessão expirou. Entre novamente para continuar com segurança.');

    renderLogin();

    expect(screen.getByRole('alert')).toHaveTextContent('Sua sessão expirou. Entre novamente para continuar com segurança.');
  });

  it('does not persist a session until the second factor succeeds', async () => {
    auth.login.mockResolvedValue({
      requiresMfa: true,
      challengeId: 'challenge-1',
      purpose: 'login',
      expiresIn: 300,
      attemptsRemaining: 5,
      recoveryAvailable: true,
      simulatedCode: '123456',
    });
    auth.verify.mockResolvedValue(authenticated);
    renderLogin();

    fireEvent.change(screen.getByPlaceholderText('seu@email.com'), { target: { value: 'admin@klm.test' } });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'senha-segura' } });
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }));

    expect(await screen.findByText('Verificação em duas etapas')).toBeInTheDocument();
    expect(auth.saveToken).not.toHaveBeenCalled();
    expect(appStore.login).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirmar código' }));

    await waitFor(() => expect(auth.verify).toHaveBeenCalledWith('challenge-1', { code: '123456' }));
    expect(await screen.findByText('Aplicação autenticada')).toBeInTheDocument();
    expect(auth.saveToken).toHaveBeenCalledWith('mfa-access-token');
    expect(appStore.login).toHaveBeenCalledOnce();
  });

  it('accepts a recovery code without falling back to the temporary code', async () => {
    auth.login.mockResolvedValue({
      requiresMfa: true,
      challengeId: 'challenge-2',
      purpose: 'login',
      expiresIn: 300,
      attemptsRemaining: 4,
      recoveryAvailable: true,
      simulatedCode: '654321',
    });
    auth.verify.mockResolvedValue(authenticated);
    renderLogin();

    fireEvent.change(screen.getByPlaceholderText('seu@email.com'), { target: { value: 'admin@klm.test' } });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'senha-segura' } });
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }));
    await screen.findByText('Verificação em duas etapas');

    fireEvent.click(screen.getByRole('button', { name: 'Usar recuperação' }));
    fireEvent.change(screen.getByPlaceholderText('XXXX-XXXX-XXXX'), { target: { value: 'RECOVERY-2026' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar código' }));

    await waitFor(() => expect(auth.verify).toHaveBeenCalledWith('challenge-2', { recoveryCode: 'RECOVERY-2026' }));
  });
});
