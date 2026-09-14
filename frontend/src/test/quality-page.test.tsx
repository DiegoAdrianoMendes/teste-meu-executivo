/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import QualityPage from '@/klm/pages/QualityPage';

const apiMocks = vi.hoisted(() => ({
  quality: vi.fn(),
  omnichannelAudit: vi.fn(),
  messages: vi.fn(),
  sendInternalMessage: vi.fn(),
  actOnOmnichannelAuditFinding: vi.fn(),
  runOmnichannelAudit: vi.fn(),
  createDomainExternalIntent: vi.fn(),
  approveExternalIntentLocally: vi.fn(),
}));

vi.mock('@/klm/api', () => ({ klmApi: apiMocks }));
vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({ can: () => true, state: {} }),
}));
vi.mock('@/klm/components/NativeAgentOperationsPanel', () => ({ NativeAgentOperationsPanel: () => null }));
vi.mock('@/klm/components/NativeModuleBar', () => ({ NativeModuleBar: () => null }));
vi.mock('@/klm/hooks/useContextualRouting', () => ({
  useContextualNavigate: () => vi.fn(),
}));

const healthyQuality = {
  score: 88,
  metrics: {},
  dimensions: [{ key: 'tone', label: 'Tom', score: 91 }],
  reviewQueue: [],
  generatedAt: '2026-09-04T12:00:00Z',
};

const healthyAudit = {
  coveragePercent: 97,
  responseCoveragePercent: 96,
  deliveryIntegrityPercent: 99,
  healthyThreads: 12,
  criticalCount: 0,
  findings: [],
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <QualityPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiMocks.messages.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('QualityPage: falha de consulta nunca vira 100%', () => {
  it('mostra indisponível e um alerta com retry quando qualidade e auditoria falham', async () => {
    apiMocks.quality.mockRejectedValueOnce(new Error('quality indisponível')).mockResolvedValue(healthyQuality);
    apiMocks.omnichannelAudit
      .mockRejectedValueOnce(new Error('audit indisponível'))
      .mockResolvedValue(healthyAudit);
    renderPage();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Não foi possível carregar as métricas de qualidade e a auditoria',
    );
    expect(screen.getAllByText('indisponível')).toHaveLength(4);
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
    expect(screen.getByText('críticas indisponíveis')).toBeInTheDocument();
    expect(screen.getByText('Não foi possível carregar as revisões pendentes.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));

    await waitFor(() => expect(apiMocks.quality).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(apiMocks.omnichannelAudit).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('97%')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('Nenhuma conversa está pendente de revisão.')).toBeInTheDocument();
  });

  it('renderiza os percentuais reais e nenhum alerta quando as consultas respondem', async () => {
    apiMocks.quality.mockResolvedValue(healthyQuality);
    apiMocks.omnichannelAudit.mockResolvedValue(healthyAudit);
    renderPage();

    expect(await screen.findByText('97%')).toBeInTheDocument();
    expect(screen.getByText('88%')).toBeInTheDocument();
    expect(screen.getByText('0 críticas')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('indisponível')).not.toBeInTheDocument();
  });
});
