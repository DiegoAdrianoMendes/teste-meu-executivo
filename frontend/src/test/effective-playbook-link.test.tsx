/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import {
  EffectivePlaybookLink,
} from '@/klm/components/playbooks/EffectivePlaybookLink';
import { normalizeEffectivePlaybookSelectors } from '@/klm/playbooks/effective-selectors';
import { ApiError } from '@/services/http';
import * as playbooks from '@/services/playbooks';
import { playbookFixture } from '@/test/playbooks-fixture';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>;
}

function renderKlm(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/app/conversas']}>
        <Routes>
          <Route path="/app/conversas" element={ui} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('EffectivePlaybookLink', () => {
  it('normalizes existing KLM aliases before resolving the published record', async () => {
    const effective = {
      ...playbookFixture,
      state: 'published' as const,
      currentVersion: 1,
      publishedVersion: 1,
    };
    const resolver = vi.spyOn(playbooks, 'getEffectivePlaybook').mockResolvedValue(effective);
    renderKlm(
      <EffectivePlaybookLink
        selectors={{
          area: 'saude_suplementar',
          channel: 'WhatsApp',
          stage: 'triagem_sdr',
          agent: 'klm_sdr',
          offer: 'Serviços advocatícios',
        }}
      />,
    );

    expect(await screen.findByText('Negativas de plano de saúde · v1')).toBeInTheDocument();
    expect(resolver).toHaveBeenCalledWith({
      area: 'negativas_plano_saude',
      channel: 'whatsapp',
      stage: 'triagem',
      agent: 'sdr',
      offer: 'servicos_advocaticios',
    });
    expect(screen.getByText('IA tria; humano reúne/fecha')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('effective-playbook-link'));
    expect(await screen.findByTestId('location-probe')).toHaveTextContent('/app/playbooks/playbook-1?tab=policy&requestChange=1');
  });

  it('shows the unassigned state without a demo fallback', async () => {
    vi.spyOn(playbooks, 'getEffectivePlaybook').mockRejectedValue(
      new ApiError(404, 'No published playbook matches this scope.'),
    );
    renderKlm(
      <EffectivePlaybookLink selectors={{ area: 'bancario' }} />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('effective-playbook-link')).toHaveAttribute('data-state', 'unassigned');
    });
    expect(screen.getByText('Nenhuma versão publicada neste contexto')).toBeInTheDocument();
    expect(screen.queryByText('Negativas de plano de saúde')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('effective-playbook-link'));
    expect(await screen.findByTestId('location-probe')).toHaveTextContent('/app/playbooks?tab=policy');
  });

  it('preserves unknown plug-and-play scope keys after safe normalization', () => {
    expect(normalizeEffectivePlaybookSelectors({
      area: 'Direito Bancário',
      stage: 'Revisão Contratual',
      agent: 'Agente Bancário',
    })).toEqual({
      area: 'direito_bancario',
      channel: undefined,
      stage: 'revisao_contratual',
      agent: 'agente_bancario',
      offer: undefined,
    });
  });
});
