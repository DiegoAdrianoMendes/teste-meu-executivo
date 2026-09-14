/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FinanceIntegrationsPanel } from '@/components/admin/FinanceIntegrationsPanel';
import type { IntegrationCatalogProvider, IntegrationConnection } from '@/types';

function catalogProvider(overrides: Partial<IntegrationCatalogProvider> = {}): IntegrationCatalogProvider {
  return {
    key: 'pluga',
    label: 'Pluga',
    category: 'automation',
    description: 'Automações no-code que conectam mais de 100 ferramentas brasileiras por webhook.',
    capabilities: ['webhooks', 'automation'],
    native: true,
    defaultWebhookPath: 'https://app.meuexecutivo.com.br/api/v1/integrations/webhooks/workspace-teste/pluga',
    docsUrl: 'https://github.com/PlugaDotCo/pluga-docs-developers',
    setupHint: {
      authType: 'webhook',
      webhookSecurity: 'assinatura_hmac_x_klm_signature',
      requiredSettings: ['webhookSecret'],
      recommendedBaseUrl: null,
      recommendedApiVersion: null,
      setupSteps: [
        'Na Pluga, crie uma automação escolhendo a ferramenta de origem como gatilho.',
        'Adicione a ação "Webhook" e cole a URL do webhook de leads deste workspace.',
        'Mapeie os campos de nome, e-mail e telefone do gatilho para o payload do webhook.',
      ],
    },
    ...overrides,
  };
}

const catalog: IntegrationCatalogProvider[] = [
  catalogProvider(),
  catalogProvider({
    key: 'meta_ads',
    label: 'Meta Ads (Lead Ads)',
    category: 'ads',
    description: 'Leads de formulários instantâneos do Facebook/Instagram via webhook leadgen.',
    docsUrl: 'https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving',
  }),
];

const integrations: IntegrationConnection[] = [];

function renderPanel() {
  return render(
    <FinanceIntegrationsPanel
      integrations={integrations}
      catalog={catalog}
      openApiSurface={null}
      loading={false}
      readOnly={false}
      onSave={vi.fn()}
    />,
  );
}

beforeEach(() => {
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('FinanceIntegrationsPanel · catálogo completo', () => {
  it('shows the full catalog grouped by category with a not-configured badge by default', () => {
    renderPanel();

    expect(screen.getByText('Catálogo completo de integrações')).toBeInTheDocument();
    expect(screen.getByText('Automação (no-code)')).toBeInTheDocument();
    expect(screen.getByText('Ads e mensuração')).toBeInTheDocument();
    expect(screen.getAllByText('Não configurado').length).toBeGreaterThan(0);
  });

  it('opens the "Como conectar" panel with numbered steps, required settings and a copyable webhook URL', async () => {
    renderPanel();

    const fullCatalogCard = screen.getByText('Catálogo completo de integrações').closest('.rounded-lg');
    expect(fullCatalogCard).not.toBeNull();
    const plugaCard = within(fullCatalogCard as HTMLElement).getByText('Pluga').closest('div.rounded-md');
    expect(plugaCard).not.toBeNull();
    fireEvent.click(within(plugaCard as HTMLElement).getByRole('button', { name: 'Como conectar' }));

    expect(await screen.findByText('Como conectar · Pluga')).toBeInTheDocument();
    const sheet = screen.getByRole('dialog');
    expect(within(sheet).getByText(/Na Pluga, crie uma automação/)).toBeInTheDocument();
    expect(within(sheet).getByText('webhookSecret')).toBeInTheDocument();
    expect(within(sheet).getByText(catalog[0].defaultWebhookPath)).toBeInTheDocument();

    fireEvent.click(within(sheet).getByRole('button', { name: 'Copiar URL do webhook' }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(catalog[0].defaultWebhookPath));
  });

  it('links to the official documentation for each provider', () => {
    renderPanel();

    const docsLinks = screen.getAllByRole('link', { name: /Documentação oficial/ });
    expect(docsLinks.some((link) => link.getAttribute('href') === catalog[0].docsUrl)).toBe(true);
  });
});
