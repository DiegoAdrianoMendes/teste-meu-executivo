/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AuthSecurityPanel } from '@/components/admin/AuthSecurityPanel';

const securityApi = vi.hoisted(() => ({
  beginAdmin: vi.fn(),
  beginEnrollment: vi.fn(),
  current: vi.fn(),
  events: vi.fn(),
  mfaStatus: vi.fn(),
  revoke: vi.fn(),
  revokeAll: vi.fn(),
  sessions: vi.fn(),
  verifyAdmin: vi.fn(),
  verifyEnrollment: vi.fn(),
}));
const deviceApi = vi.hoisted(() => ({
  list: vi.fn(),
  revoke: vi.fn(),
  revokeOthers: vi.fn(),
}));

vi.mock('@/services/auth-security', () => ({
  beginAdministrativeSession: securityApi.beginAdmin,
  beginMfaEnrollment: securityApi.beginEnrollment,
  fetchCurrentAdministrativeSession: securityApi.current,
  fetchMfaStatus: securityApi.mfaStatus,
  listAdministrativeSessions: securityApi.sessions,
  listAuthSecurityEvents: securityApi.events,
  revokeAdministrativeSession: securityApi.revoke,
  revokeAllAdministrativeSessions: securityApi.revokeAll,
  verifyAdministrativeSession: securityApi.verifyAdmin,
  verifyMfaEnrollment: securityApi.verifyEnrollment,
}));
vi.mock('@/services/auth', () => ({ saveAdministrativeSessionToken: vi.fn() }));
vi.mock('@/services/identity', () => ({
  listUserSessions: deviceApi.list,
  revokeOtherUserSessions: deviceApi.revokeOthers,
  revokeUserSession: deviceApi.revoke,
}));

function renderPanel(canAdminister = true) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthSecurityPanel canAdminister={canAdminister} />
    </QueryClientProvider>,
  );
}

function prepareBase(status = { enabled: true, simulated: true, recoveryCodesRemaining: 8 }) {
  securityApi.mfaStatus.mockResolvedValue(status);
  securityApi.current.mockResolvedValue(null);
  securityApi.sessions.mockResolvedValue([]);
  securityApi.events.mockResolvedValue([]);
  deviceApi.list.mockResolvedValue([]);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('AuthSecurityPanel', () => {
  it('shows a retry action instead of an empty state when device sessions fail to load', async () => {
    prepareBase();
    deviceApi.list.mockRejectedValueOnce(new Error('rede indisponível'));
    renderPanel();

    expect(await screen.findByText(/não foi possível carregar os dispositivos/i)).toBeInTheDocument();
    expect(screen.queryByText('Nenhuma sessão persistida.')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));

    await waitFor(() => expect(deviceApi.list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Nenhuma sessão persistida.')).toBeInTheDocument();
    expect(screen.queryByText(/não foi possível carregar os dispositivos/i)).not.toBeInTheDocument();
  });

  it('enables MFA and exposes one-time recovery codes only after verification', async () => {
    prepareBase({ enabled: false, simulated: true, recoveryCodesRemaining: 0 });
    securityApi.beginEnrollment.mockResolvedValue({
      challengeId: 'enroll-1',
      purpose: 'enrollment',
      expiresIn: 300,
      attemptsRemaining: 5,
      recoveryAvailable: false,
      simulatedCode: '111222',
    });
    securityApi.verifyEnrollment.mockResolvedValue({
      enabled: true,
      simulated: true,
      recoveryCodesRemaining: 2,
      recoveryCodes: ['RECOVERY-A', 'RECOVERY-B'],
    });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Habilitar MFA' }));
    expect(await screen.findByDisplayValue('111222')).toBeInTheDocument();
    expect(screen.queryByText('RECOVERY-A')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Confirmar habilitação' }));

    await waitFor(() => expect(securityApi.verifyEnrollment).toHaveBeenCalledWith('enroll-1', { code: '111222' }));
    expect(await screen.findByText('Códigos de recuperação, exibição única')).toBeInTheDocument();
    expect(screen.getByText('RECOVERY-A')).toBeInTheDocument();
    expect(screen.getByText('RECOVERY-B')).toBeInTheDocument();
  });

  it('creates an administrative session only after a verified challenge', async () => {
    prepareBase();
    securityApi.beginAdmin.mockResolvedValue({
      challengeId: 'admin-1',
      purpose: 'admin_session',
      expiresIn: 300,
      attemptsRemaining: 5,
      recoveryAvailable: true,
      simulatedCode: '333444',
    });
    securityApi.verifyAdmin.mockResolvedValue({
      administrativeSessionToken: 'admin-token',
      tokenType: 'admin_session',
      expiresIn: 900,
      session: {
        id: 'session-1',
        mfaMethod: 'challenge',
        expiresAt: '2026-07-19T15:15:00Z',
        lastVerifiedAt: '2026-07-19T15:00:00Z',
        revokedAt: null,
        revocationReason: null,
        createdAt: '2026-07-19T15:00:00Z',
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: 'Elevar sessão' }));
    expect(securityApi.verifyAdmin).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue('333444')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Confirmar elevação' }));
    await waitFor(() => expect(securityApi.verifyAdmin).toHaveBeenCalledWith('admin-1', { code: '333444' }));
  });

  it('shows the one-time TOTP provisioning key outside local simulation', async () => {
    prepareBase({ enabled: false, simulated: false, recoveryCodesRemaining: 0 });
    securityApi.beginEnrollment.mockResolvedValue({
      challengeId: 'totp-enroll-1',
      purpose: 'enrollment',
      expiresIn: 300,
      attemptsRemaining: 5,
      recoveryAvailable: false,
      simulatedCode: null,
      manualEntryKey: 'JBSWY3DPEHPK3PXP',
      provisioningUri: 'otpauth://totp/Meu%20Executivo%3Aroot%40example.com?secret=JBSWY3DPEHPK3PXP',
    });
    renderPanel();

    expect(await screen.findByText('TOTP de produção')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Habilitar MFA' }));

    expect(await screen.findByTestId('mfa-manual-key')).toHaveTextContent('JBSWY3DPEHPK3PXP');
    expect(screen.getByRole('link', { name: 'Abrir no autenticador deste dispositivo' })).toHaveAttribute(
      'href',
      expect.stringContaining('otpauth://totp/'),
    );
    expect(screen.queryByText(/Simulador local:/)).not.toBeInTheDocument();
  });

  it('shows normal device sessions to a non-admin and revokes another device', async () => {
    prepareBase();
    deviceApi.list.mockResolvedValue([
      {
        id: 'current-session',
        deviceLabel: 'Chrome em macOS',
        deviceType: 'desktop',
        browser: 'Chrome',
        operatingSystem: 'macOS',
        createdAt: '2026-07-19T14:00:00Z',
        lastSeenAt: '2026-07-19T15:00:00Z',
        expiresAt: '2026-08-19T15:00:00Z',
        revokedAt: null,
        current: true,
      },
      {
        id: 'other-session',
        deviceLabel: 'Firefox em Linux',
        deviceType: 'desktop',
        browser: 'Firefox',
        operatingSystem: 'Linux',
        createdAt: '2026-07-18T14:00:00Z',
        lastSeenAt: '2026-07-18T15:00:00Z',
        expiresAt: '2026-08-18T15:00:00Z',
        revokedAt: null,
        current: false,
      },
    ]);
    deviceApi.revoke.mockResolvedValue(undefined);
    renderPanel(false);

    expect(await screen.findByText('Chrome em macOS')).toBeInTheDocument();
    expect(screen.getByText('Firefox em Linux')).toBeInTheDocument();
    expect(screen.queryByText('Sessão administrativa')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Desconectar Firefox em Linux' }));
    await waitFor(() => expect(deviceApi.revoke).toHaveBeenCalledWith('other-session'));
  });
});
