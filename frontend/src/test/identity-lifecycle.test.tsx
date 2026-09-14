/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import Checkout from '@/pages/Checkout';
import ThankYou from '@/pages/ThankYou';
import ForgotPassword from '@/pages/ForgotPassword';
import AcceptInvitation from '@/pages/AcceptInvitation';
import ResetPassword from '@/pages/ResetPassword';
import VerifyAccount from '@/pages/VerifyAccount';
import { AppProvider } from '@/store/app-context';
import { passwordByteLength, passwordPolicyError } from '@/services/identity';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function VerificationProbe() {
  const location = useLocation();
  return <p>Rota de verificação {location.search}</p>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('identity lifecycle frontend', () => {
  it('submits Checkout with idempotency and routes to pending verification', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        items: [{
          sku: 'crm_advanced', name: 'Advanced', description: 'Crescimento estruturado',
          family: 'crm', kind: 'base_plan', isRecommended: true, isQuoteOnly: false,
          priceMonthlyCents: 67900, currency: 'BRL', cta: 'Quero uma demonstração',
          highlights: ['10 usuários ativos'],
        }],
        generatedAt: '2026-08-12T12:00:00Z', state: 'available',
      }))
      .mockResolvedValueOnce(jsonResponse({
      requestId: 'request-1',
      status: 'accepted',
      message: 'Solicitação aceita.',
      deliveryMode: 'transactional_email',
      deliveryId: 'delivery-1',
      expiresAt: '2026-07-19T16:00:00Z',
      }, 202));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter initialEntries={['/checkout?plan=pro']}>
        <Routes>
          <Route path="/checkout" element={<Checkout />} />
          <Route path="/verify-account" element={<VerificationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Plano Advanced')).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('Dra. Ana Souza'), { target: { value: 'Ana Souza' } });
    fireEvent.change(screen.getByPlaceholderText('ana@escritorio.com.br'), { target: { value: 'ana@example.com' } });
    fireEvent.change(screen.getByPlaceholderText('Souza Advogados'), { target: { value: 'Souza Legal' } });
    fireEvent.change(screen.getByPlaceholderText('10 a 72 bytes em UTF-8'), { target: { value: 'SenhaForte10' } });
    fireEvent.click(screen.getByRole('button', { name: /continuar para verificação/i }));

    expect(await screen.findByText(/mode=transactional_email/)).toBeInTheDocument();
    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(init.headers).get('Idempotency-Key')).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      name: 'Ana Souza',
      email: 'ana@example.com',
      workspaceName: 'Souza Legal',
      password: 'SenhaForte10',
      plan: 'crm_advanced',
    });
  });

  it('reports transactional reset delivery without exposing a local secret', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      requestId: 'request-2',
      status: 'accepted',
      message: 'Se aplicável, a redefinição foi preparada.',
      deliveryMode: 'transactional_email',
    }, 202));
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryRouter><ForgotPassword /></MemoryRouter>);
    const brand = screen.getByRole('img', { name: 'Meu Executivo' });
    expect(brand).toHaveAttribute('src', '/brand/meu-executivo/wordmark.svg');
    expect(brand).toHaveAttribute('data-brand-logo', 'wordmark');
    expect(brand).not.toHaveAttribute('src', '/logo.png');
    fireEvent.change(screen.getByLabelText('E-mail'), { target: { value: 'ana@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar solicitação' }));

    expect(await screen.findByText('Solicitação aceita')).toBeInTheDocument();
    expect(screen.getByText(/instruções foram enviadas por e-mail/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/entrega simulada/i)).not.toBeInTheDocument();
  });

  it('does not query the privileged local outbox for transactional signup delivery', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).includes('/auth/register/resend')) {
        return jsonResponse({
          requestId: 'request-resend-1',
          status: 'accepted',
          message: 'Se o cadastro puder prosseguir, enviaremos as instruções.',
          deliveryMode: 'transactional_email',
          expiresAt: '2026-08-12T23:30:00Z',
        }, 202);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter initialEntries={['/verify-account?delivery=delivery-1&mode=transactional_email']}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <AppProvider><VerifyAccount /></AppProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(screen.getByText(/confira seu e-mail para ativar a conta/i)).toBeInTheDocument();
    expect(screen.queryByText(/carregando entrega local/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/código de verificação/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /verificar e entrar/i })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/auth/local-outbox/'))).toBe(false);
    fireEvent.change(screen.getByLabelText(/não recebeu/i), { target: { value: 'ana@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: /reenviar verificação/i }));
    expect(await screen.findByRole('button', { name: /reenvio registrado/i })).toBeDisabled();
    const resendCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/auth/register/resend'));
    expect(resendCall).toBeTruthy();
    expect(JSON.parse(String((resendCall?.[1] as RequestInit).body))).toEqual({ email: 'ana@example.com' });
  });

  it('shows the verified canonical contract and honest onboarding next steps', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      contract: {
        id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Souza Legal',
        status: 'trialing', currency: 'BRL', version: 1,
        lines: [{ sku: 'crm_advanced', contractedPriceCents: 67900, billingInterval: 'monthly', status: 'active' }],
        price: { listPriceCents: 67900, contractedPriceCents: 67900, recurringPriceCents: 67900, invoiceCreditCents: 0, amountDueCents: 67900 },
        createdAt: '2026-08-12T12:00:00Z', updatedAt: '2026-08-12T12:00:00Z',
      },
      requests: [], automaticCheckoutAvailable: false, billingPortalAvailable: false, providerSubscriptionLinked: false, providerSubscriptionStatus: null, providerState: 'not_configured', availableProviders: [],
      providerMessage: 'Cobrança automática ainda não vinculada.',
    }));
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryRouter><ThankYou /></MemoryRouter>);

    expect(await screen.findByText(/Plano Advanced · trialing/i)).toBeInTheDocument();
    expect(screen.getByText(/nenhum QR code é necessário/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /iniciar configuração do workspace/i })).toHaveAttribute('href', '/onboarding');
    expect(screen.queryByText(/pagamento confirmado/i)).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /concluir contratação/i })).toBeInTheDocument();
    expect(screen.getByText(/ativação assistida/i)).toBeInTheDocument();
  });

  it('hands a verified trial to a configured hosted checkout without inventing payment success', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        contract: {
          id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Souza Legal',
          status: 'trialing', currency: 'BRL', version: 3,
          lines: [{ sku: 'crm_advanced', contractedPriceCents: 67900, billingInterval: 'monthly', status: 'active' }],
          price: { listPriceCents: 67900, contractedPriceCents: 67900, recurringPriceCents: 67900, invoiceCreditCents: 0, amountDueCents: 67900 },
          createdAt: '2026-08-12T12:00:00Z', updatedAt: '2026-08-12T12:00:00Z',
        },
        requests: [], automaticCheckoutAvailable: true, billingPortalAvailable: false,
        providerSubscriptionLinked: false, providerSubscriptionStatus: null,
        providerState: 'configured', providerMessage: 'Checkout disponível.',
        activeProvider: null, availableProviders: ['asaas', 'mercado_pago'],
      }))
      .mockResolvedValueOnce(jsonResponse({
        intentId: 'intent-1', status: 'session_created',
        checkoutUrl: 'https://checkout.example/session-1', provider: 'asaas',
      }, 201));
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryRouter><ThankYou /></MemoryRouter>);

    expect(await screen.findByRole('combobox', { name: /meio de pagamento da contratação/i })).toHaveTextContent(/Asaas/i);
    fireEvent.click(screen.getByRole('button', { name: /preparar pagamento seguro/i }));
    const paymentLink = await screen.findByRole('link', { name: /abrir pagamento seguro/i });
    expect(paymentLink).toHaveAttribute('href', 'https://checkout.example/session-1');
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toContain('/commercial/account/checkout-sessions');
    expect(new Headers(init.headers).get('Idempotency-Key')).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual(expect.objectContaining({
      targetSku: 'crm_advanced', expectedContractVersion: 3, provider: 'asaas',
    }));
    expect(screen.queryByText(/pagamento confirmado/i)).not.toBeInTheDocument();
  });

  it('distinguishes a checkout return from account verification without inventing payment confirmation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      contract: {
        id: 'contract-1', workspaceId: 'workspace-1', workspaceLabel: 'Souza Legal',
        status: 'trialing', currency: 'BRL', version: 1,
        lines: [{ sku: 'crm_advanced', contractedPriceCents: 67900, billingInterval: 'monthly', status: 'active' }],
        price: { listPriceCents: 67900, contractedPriceCents: 67900, recurringPriceCents: 67900, invoiceCreditCents: 0, amountDueCents: 67900 },
        createdAt: '2026-08-12T12:00:00Z', updatedAt: '2026-08-12T12:00:00Z',
      },
      requests: [], automaticCheckoutAvailable: true, billingPortalAvailable: false, providerSubscriptionLinked: false, providerSubscriptionStatus: null, providerState: 'configured',
      providerMessage: 'Checkout disponível.', activeProvider: null, availableProviders: ['stripe'],
    }));
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryRouter initialEntries={['/obrigado?checkout=success']}><ThankYou /></MemoryRouter>);

    expect(await screen.findByRole('heading', { name: /checkout concluído/i })).toBeInTheDocument();
    expect(screen.getByText(/confirmação da assinatura será refletida/i)).toBeInTheDocument();
    expect(screen.queryByText(/pagamento confirmado/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/identidade foi verificada/i)).not.toBeInTheDocument();
  });

  it('accepts an existing identity invitation without asking for a password', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        invitationId: 'invitation-1',
        workspaceName: 'Equipe A',
        recipientHint: 'a***@example.com',
        status: 'pending',
        expiresAt: '2026-07-20T16:00:00Z',
        requiresPassword: false,
      }))
      .mockResolvedValueOnce(jsonResponse({
        invitationId: 'invitation-1',
        membershipId: 'membership-1',
        status: 'accepted',
        requiresLogin: true,
        message: 'Convite aceito.',
      }));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter initialEntries={['/accept-invitation/invitation-1?token=invitation-token-value']}>
        <Routes>
          <Route path="/accept-invitation/:invitationId" element={<AcceptInvitation />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Entrar em Equipe A')).toBeInTheDocument();
    expect(screen.queryByLabelText(/senha/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Aceitar convite' }));
    expect(await screen.findByText('Convite aceito')).toBeInTheDocument();

    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(init.headers).get('Idempotency-Key')).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({ token: 'invitation-token-value' });
  });

  it('submits reset by delivery code and enforces the UTF-8 byte policy', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ message: 'Senha redefinida.' }));
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter initialEntries={['/reset-password?delivery=delivery-3']}>
        <ResetPassword />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText('Código local'), { target: { value: '881920' } });
    fireEvent.change(screen.getByLabelText('Nova senha'), { target: { value: 'SenhaNova10' } });
    fireEvent.change(screen.getByLabelText('Confirmar nova senha'), { target: { value: 'SenhaNova10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Redefinir senha' }));

    expect(await screen.findByText('Senha redefinida')).toBeInTheDocument();
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({
      deliveryId: 'delivery-3',
      code: '881920',
      newPassword: 'SenhaNova10',
    });
    expect(passwordByteLength('á'.repeat(36))).toBe(72);
    expect(passwordPolicyError('á'.repeat(36))).toBeNull();
    expect(passwordPolicyError('á'.repeat(37))).toMatch(/máximo 72 bytes/i);
  });
});
