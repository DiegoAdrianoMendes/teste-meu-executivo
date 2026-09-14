import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MetaCommercialChargePanel } from '@/components/admin/MetaCommercialChargePanel';
import type { WhatsAppCommercialChargeSummary } from '@/types';

const summary: WhatsAppCommercialChargeSummary = {
  workspaceId: 'workspace-1',
  requestedCampaignId: null,
  generatedAt: '2026-07-22T12:00:00Z',
  chargeBasis: 'delivered_messages',
  internalProviderCostExposed: false,
  campaigns: [{
    campaignId: 'campaign-1',
    title: 'Retomada de contatos',
    templateCategory: 'marketing',
    eligibleRecipients: 100,
    confirmedRecipients: 80,
    commercialChargeLines: [],
    totalsByCurrency: [],
    missingRates: [],
  }],
  totalsByCurrency: [{
    currency: 'BRL',
    estimatedRecipientCount: 100,
    confirmedRecipientCount: 80,
    estimatedCommercialCharge: '25.00000000',
    confirmedCommercialCharge: '20.00000000',
  }],
  missingRates: [],
};

describe('MetaCommercialChargePanel', () => {
  it('separa a cobrança comercial confirmada da projeção e dos tokens de IA', () => {
    render(<MetaCommercialChargePanel value={summary} />);

    expect(screen.getByText('Cobrança da API oficial da Meta')).toBeInTheDocument();
    expect(screen.getByText(/R\$\s*20,00/)).toBeInTheDocument();
    expect(screen.getByText(/Projeção da audiência:/)).toHaveTextContent('R$ 25,00');
    expect(screen.getByText(/Valor comercial integral/)).toHaveTextContent('separado da franquia de tokens de IA');
    expect(screen.queryByText(/custo interno medido/i)).not.toBeInTheDocument();
  });

  it('não inventa valor quando falta tarifa vigente', () => {
    render(<MetaCommercialChargePanel value={{
      ...summary,
      totalsByCurrency: [],
      missingRates: [{
        campaignId: 'campaign-1',
        templateCategory: 'marketing',
        recipientCountryCode: 'BR',
        countryResolution: 'phone_prefix_55',
        chargeStage: 'estimated',
        recipientCount: 100,
        rateReferenceFrom: '2026-07-22T12:00:00Z',
        rateReferenceTo: '2026-07-22T12:00:00Z',
        reason: 'no_effective_rate',
        candidateRateCardKeys: [],
      }],
    }} />);

    expect(screen.getByText('Tarifa pendente')).toBeInTheDocument();
    expect(screen.getByText(/100 mensagens sem tarifa aplicável/)).toBeInTheDocument();
    expect(screen.getByText(/nenhum valor será inventado/i)).toBeInTheDocument();
  });

  it('expõe falha local e permite nova tentativa', () => {
    const onRetry = vi.fn();
    render(<MetaCommercialChargePanel value={null} error="Serviço indisponível." onRetry={onRetry} />);

    fireEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
