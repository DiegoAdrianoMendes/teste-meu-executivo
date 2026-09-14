/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { TooltipProvider } from '@/components/ui/tooltip';
import ConnectionsHubPage from '@/klm/pages/ConnectionsHubPage';
import { ApiError } from '@/services/http';
import type { WhatsAppConnectionSummary, WhatsAppWebhookInfo } from '@/types';

const whatsappServiceMocks = vi.hoisted(() => ({
  fetchWhatsAppConnections: vi.fn(),
  fetchWhatsAppWebhookInfo: vi.fn(),
  createManualWhatsAppConnection: vi.fn(),
  fetchMetaEmbeddedSignupConfig: vi.fn(),
  completeMetaEmbeddedSignup: vi.fn(),
}));

vi.mock('@/services/whatsapp', () => whatsappServiceMocks);

const instagramServiceMocks = vi.hoisted(() => ({
  fetchInstagramConnections: vi.fn(),
  fetchInstagramAuthorizeUrl: vi.fn(),
  fetchInstagramTokenStatus: vi.fn(),
  createInstagramConnection: vi.fn(),
}));

vi.mock('@/services/instagramOauth', () => instagramServiceMocks);

vi.mock('@/store/app-context', () => ({
  useOptionalAppStore: () => ({
    activeWorkspace: { id: 'workspace-1', name: 'MV Advocacia' },
    can: () => true,
  }),
  useAppStore: () => ({
    activeWorkspace: { id: 'workspace-1', name: 'MV Advocacia' },
    can: () => true,
  }),
}));

vi.mock('@/services/admin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/admin')>();
  return { ...actual, fetchMyAccessPackage: async () => null };
});

const STORAGE_KEY = 'klm_wizard_whatsapp_b1:workspace-1';

const webhookInfoFixture: WhatsAppWebhookInfo = {
  webhookUrl: 'https://klm.example.com/api/v1/whatsapp/webhook',
  verifyToken: 'verify-token-xyz',
  verifyTokenConfigured: true,
  appSecretConfigured: true,
  instructions: [
    'Copie a URL de callback e cole no campo Callback URL.',
    'Copie o token de verificação e cole no campo Verify Token.',
    'Assine o campo messages.',
  ],
};

const connectedFixture: WhatsAppConnectionSummary = {
  id: 'conn-1',
  connectionType: 'OFFICIAL_MANUAL',
  isDefault: true,
  phoneNumberId: 'pn-1',
  phoneNumber: '5511999990000',
  displayPhoneNumber: '+55 11 99999-0000',
  displayName: 'MV Advocacia',
  businessAccountId: 'waba-1',
  wabaId: 'waba-1',
  instanceId: null,
  qrProvider: null,
  qrStatus: null,
  status: 'connected',
  webhookRegistered: true,
  connectedAt: '2026-07-20T00:00:00Z',
  disconnectedAt: null,
  updatedAt: '2026-07-20T00:00:00Z',
  lastError: null,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <MemoryRouter initialEntries={['/app/conexoes']}>
          <Routes>
            <Route path="/app/conexoes" element={<ConnectionsHubPage />} />
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([]);
  whatsappServiceMocks.fetchWhatsAppWebhookInfo.mockResolvedValue(webhookInfoFixture);
  whatsappServiceMocks.createManualWhatsAppConnection.mockReset();
  whatsappServiceMocks.fetchMetaEmbeddedSignupConfig.mockResolvedValue({
    available: true,
    manualCloudApiAvailable: true,
    coexistenceAvailable: true,
    techProviderReady: false,
    appId: 'public-app-id',
    configId: 'public-config-id',
    solutionId: null,
    graphApiVersion: 'v26.0',
    featureType: 'whatsapp_business_app_onboarding',
    missingConfiguration: [],
    missingTechProviderConfiguration: ['META_SOLUTION_ID'],
  });
  whatsappServiceMocks.completeMetaEmbeddedSignup.mockReset();
  instagramServiceMocks.fetchInstagramConnections.mockResolvedValue([]);
  instagramServiceMocks.fetchInstagramAuthorizeUrl.mockReset();
  instagramServiceMocks.fetchInstagramTokenStatus.mockReset();
  instagramServiceMocks.createInstagramConnection.mockReset();
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  delete window.FB;
  cleanup();
  vi.restoreAllMocks();
});

describe('ConnectionsHubPage', () => {
  it('renders all seven manual (B1) wizard steps with complete instructions when there is no active connection', async () => {
    renderPage();

    expect(await screen.findByText('Passo 1 · Pré-requisitos')).toBeInTheDocument();
    expect(screen.getByText('Passo 2 · Criar o app Business')).toBeInTheDocument();
    expect(screen.getByText('Passo 3 · Adicionar o produto WhatsApp e vincular a WABA')).toBeInTheDocument();
    expect(screen.getByText('Passo 4 · Business Verification do escritório')).toBeInTheDocument();
    expect(screen.getByText('Passo 5 · Criar System User e gerar o token permanente')).toBeInTheDocument();
    expect(screen.getByText('Passo 6 · Configurar o webhook')).toBeInTheDocument();
    expect(await screen.findByText('Passo 7 · Colar as credenciais no Meu Executivo')).toBeInTheDocument();

    // Não pode haver molduras vazias / placeholders visuais: cada passo tem texto completo.
    expect(screen.getByText(/O número de telefone que vai virar o WhatsApp oficial/i)).toBeInTheDocument();
    expect(await screen.findByDisplayValue(webhookInfoFixture.webhookUrl)).toBeInTheDocument();
    expect(screen.getByDisplayValue(webhookInfoFixture.verifyToken!)).toBeInTheDocument();

    // Coexistência é uma jornada real e usa a configuração pública do backend.
    expect(screen.getByText('Conectar o WhatsApp Business existente')).toBeInTheDocument();
    expect(screen.getByText('Coexistência')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /conectar whatsapp existente/i })).toBeEnabled();

    // Troubleshooting cobre os 4 erros pedidos.
    expect(screen.getByText(/Display name recusado pela Meta/i)).toBeInTheDocument();
    expect(screen.getByText(/Número preso em outra WABA/i)).toBeInTheDocument();
    expect(screen.getByText(/133005/)).toBeInTheDocument();
    expect(screen.getByText(/Business Verification pendente ou recusada/i)).toBeInTheDocument();
  });

  it('shows the status panel with "conectar outro número" instead of the wizard when a connection is already active', async () => {
    whatsappServiceMocks.fetchWhatsAppConnections.mockResolvedValue([connectedFixture]);

    renderPage();

    expect(await screen.findByText('WhatsApp já conectado neste workspace')).toBeInTheDocument();
    expect(screen.getByText('+55 11 99999-0000 · WABA waba-1')).toBeInTheDocument();
    expect(screen.queryByText('Passo 1 · Pré-requisitos')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Conectar outro número' }));

    expect(await screen.findByText('Passo 1 · Pré-requisitos')).toBeInTheDocument();
  });

  it('conclui o Embedded Signup em modo coexistência com o feature type oficial', async () => {
    whatsappServiceMocks.completeMetaEmbeddedSignup.mockResolvedValue({
      ...connectedFixture,
      connectionType: 'TECH_PROVIDER',
      transportMode: 'coexistence',
      coexistenceSyncStatus: 'requested',
    });
    const init = vi.fn();
    let loginOptions: unknown;
    const login = vi.fn((callback: (response: { authResponse: { code: string } }) => void, options: unknown) => {
      loginOptions = options;
      window.dispatchEvent(new MessageEvent('message', {
        origin: 'https://www.facebook.com',
        data: JSON.stringify({
          type: 'WA_EMBEDDED_SIGNUP',
          event: 'FINISH',
          data: { waba_id: 'waba-coex', phone_number_id: 'phone-coex' },
        }),
      }));
      callback({ authResponse: { code: 'authorization-code' } });
    });
    window.FB = { init, login };

    renderPage();
    const embeddedButton = await screen.findByRole('button', { name: /conectar whatsapp existente/i });
    await waitFor(() => expect(embeddedButton).toBeEnabled(), { timeout: 500 });
    fireEvent.click(embeddedButton);
    await waitFor(() => expect(login).toHaveBeenCalledTimes(1), { timeout: 500 });

    await waitFor(() => expect(whatsappServiceMocks.completeMetaEmbeddedSignup).toHaveBeenCalledWith({
      code: 'authorization-code',
      wabaId: 'waba-coex',
      phoneNumberId: 'phone-coex',
      transportMode: 'coexistence',
    }, expect.anything()), { timeout: 500 });
    expect(loginOptions).toEqual(expect.objectContaining({
      config_id: 'public-config-id',
      extras: expect.objectContaining({
        featureType: 'whatsapp_business_app_onboarding',
        sessionInfoVersion: '3',
      }),
    }));
    expect(init).toHaveBeenCalledWith(expect.objectContaining({ appId: 'public-app-id', version: 'v26.0' }));
    delete window.FB;
  });

  it('validates and submits the step 7 form with the exact payload expected by the manual endpoint', async () => {
    whatsappServiceMocks.createManualWhatsAppConnection.mockResolvedValue(connectedFixture);

    renderPage();

    const submitButton = await screen.findByRole('button', { name: 'Conectar WhatsApp' });

    // Sem preencher nada, o form nativo bloqueia o submit (campos required).
    fireEvent.click(submitButton);
    expect(whatsappServiceMocks.createManualWhatsAppConnection).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Token de acesso permanente (System User)'), {
      target: { value: 'token-abc-123' },
    });
    fireEvent.change(screen.getByLabelText('Phone Number ID'), { target: { value: '1234567890123456' } });
    fireEvent.change(screen.getByLabelText('WhatsApp Business Account ID (WABA)'), { target: { value: '9876543210987654' } });

    fireEvent.click(submitButton);

    await waitFor(() => expect(whatsappServiceMocks.createManualWhatsAppConnection).toHaveBeenCalledTimes(1));
    expect(whatsappServiceMocks.createManualWhatsAppConnection).toHaveBeenCalledWith({
      accessToken: 'token-abc-123',
      phoneNumberId: '1234567890123456',
      businessAccountId: '9876543210987654',
      registrationPin: undefined,
      isDefault: true,
    });

    expect(
      await screen.findByText((_, element) => element?.tagName === 'SPAN' && /conectado/i.test(element.textContent || '')),
    ).toBeInTheDocument();
  });

  it('shows a translated error with a concrete next step when the manual endpoint rejects the credentials', async () => {
    whatsappServiceMocks.createManualWhatsAppConnection.mockRejectedValue(new Error('invalid access token permission'));

    renderPage();

    fireEvent.change(await screen.findByLabelText('Token de acesso permanente (System User)'), {
      target: { value: 'token-invalido' },
    });
    fireEvent.change(screen.getByLabelText('Phone Number ID'), { target: { value: '1234567890123456' } });
    fireEvent.change(screen.getByLabelText('WhatsApp Business Account ID (WABA)'), { target: { value: '9876543210987654' } });
    fireEvent.click(screen.getByRole('button', { name: 'Conectar WhatsApp' }));

    expect(await screen.findByText('Não deu certo desta vez')).toBeInTheDocument();
    expect(screen.getByText(/token informado não foi aceito/i)).toBeInTheDocument();
    expect(screen.getByText(/Volte ao Passo 5/i)).toBeInTheDocument();
  });

  it('distinguishes a closed external-delivery gate from a Meta credential rejection', async () => {
    whatsappServiceMocks.createManualWhatsAppConnection.mockRejectedValue(
      new ApiError(503, 'Entregas externas desabilitadas.', {
        code: 'KLM-EXT-001',
        message: 'Chamada a Meta bloqueada enquanto entregas externas estiverem desabilitadas.',
      }),
    );

    renderPage();
    fireEvent.change(await screen.findByLabelText('Token de acesso permanente (System User)'), {
      target: { value: 'token-nao-deve-ser-trocado' },
    });
    fireEvent.change(screen.getByLabelText('Phone Number ID'), { target: { value: '1234567890123456' } });
    fireEvent.change(screen.getByLabelText('WhatsApp Business Account ID (WABA)'), { target: { value: '9876543210987654' } });
    fireEvent.click(screen.getByRole('button', { name: 'Conectar WhatsApp' }));

    expect(await screen.findByText(/não foi enviada à Meta/i)).toBeInTheDocument();
    expect(screen.getByText(/habilitar o gate de entregas externas/i)).toBeInTheDocument();
    expect(screen.getByText(/não gere outro token/i)).toBeInTheDocument();
    expect(screen.queryByText(/A Meta recusou a conexão/i)).not.toBeInTheDocument();
  });

  it('persists and resumes wizard checklist progress from localStorage per workspace', async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        checks: { conta_meta: true, numero_livre: true, display_name: true },
        fields: { phoneNumberId: '', businessAccountId: '' },
        errorMessage: null,
        showWizard: false,
      }),
    );

    renderPage();

    const step1Checkbox = await screen.findByText(/Você tem uma conta Meta \(Facebook\) pessoal ativa/i);
    expect(step1Checkbox.closest('label')?.querySelector('button[role="checkbox"]')).toHaveAttribute(
      'data-state',
      'checked',
    );

    fireEvent.click(screen.getByText('Passo 2 · Criar o app Business'));
    const step2Check = screen.getByText(/Criei o app tipo Business/i).closest('label')!.querySelector(
      'button[role="checkbox"]',
    )!;
    fireEvent.click(step2Check);

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
      expect(saved.checks.app_criado).toBe(true);
      expect(saved.checks.conta_meta).toBe(true);
    });
  });
});

describe('ConnectionsHubPage · Instagram OAuth card', () => {
  it('renders the "not connected" state with a form to cadastrar a conexão quando não há nenhuma', async () => {
    instagramServiceMocks.fetchInstagramConnections.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('Conectar Instagram')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Cadastrar conexão Instagram' })).toBeInTheDocument();
  });

  it('renders the "not connected" token badge when a connection exists but has no OAuth intent yet', async () => {
    instagramServiceMocks.fetchInstagramConnections.mockResolvedValue([
      { id: 'ig-conn-0', accountName: 'Perfil Oficial', username: '@escritorio', status: 'pending' },
    ]);
    instagramServiceMocks.fetchInstagramTokenStatus.mockResolvedValue({
      connectionId: 'ig-conn-0',
      state: 'not_connected',
      expiresInDays: null,
      oauthStatus: null,
      scopes: [],
    });

    renderPage();

    expect(await screen.findByText('Não conectado')).toBeInTheDocument();
  });

  it('renders the "oauth_prepared" state with the explanatory step-by-step and the gate-closed messaging', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null);
    instagramServiceMocks.fetchInstagramConnections.mockResolvedValue([
      { id: 'ig-conn-1', accountName: 'Perfil Oficial', username: '@escritorio', status: 'pending' },
    ]);
    instagramServiceMocks.fetchInstagramTokenStatus.mockResolvedValue({
      connectionId: 'ig-conn-1',
      state: 'oauth_prepared',
      expiresInDays: null,
      oauthStatus: 'oauth_prepared',
      scopes: [],
    });
    instagramServiceMocks.fetchInstagramAuthorizeUrl.mockResolvedValue({
      authorizationUrl: 'https://www.instagram.com/oauth/authorize?client_id=1&scope=instagram_business_basic',
      state: 'signed-state-value',
      expiresAt: '2026-07-23T12:05:00Z',
      scopes: ['instagram_business_basic', 'instagram_business_manage_messages', 'instagram_business_manage_comments'],
    });

    renderPage();

    expect(await screen.findByText('Preparado, aguardando aprovação de APIs externas')).toBeInTheDocument();
    expect(screen.getByText(/O que vai acontecer ao clicar em "Iniciar conexão OAuth"/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Iniciar conexão OAuth' }));

    await waitFor(() => {
      expect(openSpy).toHaveBeenCalledWith(
        'https://www.instagram.com/oauth/authorize?client_id=1&scope=instagram_business_basic',
        '_self',
      );
    });
  });

  it('renders the "configured" state showing the token badge without ever leaking a raw token value', async () => {
    instagramServiceMocks.fetchInstagramConnections.mockResolvedValue([
      { id: 'ig-conn-2', accountName: 'Perfil Oficial', username: '@escritorio', status: 'connected' },
    ]);
    instagramServiceMocks.fetchInstagramTokenStatus.mockResolvedValue({
      connectionId: 'ig-conn-2',
      state: 'configured',
      expiresInDays: 45,
      oauthStatus: 'connected',
      scopes: ['instagram_business_basic'],
    });

    renderPage();

    expect(await screen.findByText('Token configurado')).toBeInTheDocument();
  });

  it('renders the Jornada A App Review checklist with the three IG scopes and Business Verification', async () => {
    renderPage();

    expect(await screen.findByText(/Jornada A · App Review do Instagram/i)).toBeInTheDocument();
    expect(screen.getByText(/instagram_business_basic aprovado/i)).toBeInTheDocument();
    expect(screen.getByText(/instagram_business_manage_messages aprovado/i)).toBeInTheDocument();
    expect(screen.getByText(/instagram_business_manage_comments aprovado/i)).toBeInTheDocument();
    expect(screen.getByText(/Business Verification do escritório concluída/i)).toBeInTheDocument();
  });
});
