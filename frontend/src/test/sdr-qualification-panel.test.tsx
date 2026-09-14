/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { klmApi, type KlmSdrQualification, type KlmSdrStatus } from '@/klm/api';
import { SdrQualificationPanel } from '@/klm/components/SdrQualificationPanel';

const MATTER_ID = '11111111-2222-3333-4444-555555555555';

function fullQualification(): KlmSdrQualification {
  return {
    client_name: 'Joana Ribeiro da Silva',
    phone: '+55 61 99999-0000',
    demand_type: 'Rescisão indireta',
    employer: 'Transportadora Alfa LTDA',
    time_at_company: '3 anos e 4 meses',
    employment_situation: 'empregada',
    has_ctps: true,
    has_documents: false,
    documents_requested: ['CTPS', 'Contracheques'],
    documents_received: ['CTPS'],
    qualification_score: 8.5,
    classification: 'Forte',
    estimated_claim_value: 42000,
    viability_notes: 'Atrasos reiterados de salário comprovados por contracheque.',
    handoff_target: 'closer_juridico',
    handoff_notes: 'Cliente disponível para reunião à tarde.',
    current_step: 'qualificado',
    completed: true,
    conversation_summary: 'Lead relatou atraso salarial de dois meses.',
  };
}

/** Reproduz o efeito de `mask_sdr_status` (app/core/lead_privacy.py). */
function maskedStatus(): KlmSdrStatus {
  const qualification = fullQualification();
  return {
    matter_id: MATTER_ID,
    sdr_step: 'qualificado',
    sdr_completed: true,
    qualification: {
      ...qualification,
      client_name: 'Lead 555555',
      phone: null,
      employer: null,
      viability_notes: '[conteúdo protegido]',
      handoff_notes: '[conteúdo protegido]',
      conversation_summary: '[conteúdo protegido]',
    },
  };
}

function renderPanel(props: Partial<Parameters<typeof SdrQualificationPanel>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(
    <SdrQualificationPanel
      matterId={MATTER_ID}
      canViewSensitiveLeadData
      canReprocess
      {...props}
    />,
    { wrapper },
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Painel de qualificação do SDR no Caso 360', () => {
  it('renderiza a qualificação estruturada que o agente extraiu', async () => {
    vi.spyOn(klmApi, 'sdrStatus').mockResolvedValue({
      matter_id: MATTER_ID,
      sdr_step: 'qualificado',
      sdr_completed: true,
      qualification: fullQualification(),
    });

    renderPanel();

    expect(await screen.findByText('Joana Ribeiro da Silva')).toBeInTheDocument();
    expect(screen.getByText('Rescisão indireta')).toBeInTheDocument();
    expect(screen.getByText('Transportadora Alfa LTDA')).toBeInTheDocument();
    expect(screen.getByText('3 anos e 4 meses')).toBeInTheDocument();
    expect(screen.getByText('8.5/10')).toBeInTheDocument();
    expect(screen.getByText('Forte')).toBeInTheDocument();
    expect(screen.getByText('Closer jurídico')).toBeInTheDocument();
    expect(screen.getByText('Contracheques')).toBeInTheDocument();
    expect(screen.getByText('Cliente disponível para reunião à tarde.')).toBeInTheDocument();
    // Passo e conclusão da qualificação.
    expect(screen.getByText('Passo: Qualificado')).toBeInTheDocument();
    expect(screen.getByText('Qualificação concluída')).toBeInTheDocument();
    // Sem aviso de mascaramento quando o usuário pode ver dado sensível.
    expect(screen.queryByText(/Leitura mascarada por permissão/)).not.toBeInTheDocument();
  });

  it('mostra estado vazio explícito quando o lead ainda não foi qualificado', async () => {
    vi.spyOn(klmApi, 'sdrStatus').mockResolvedValue({
      matter_id: MATTER_ID,
      sdr_step: 'inicio',
      sdr_completed: false,
      qualification: null,
    });

    renderPanel();

    expect(await screen.findByText(/ainda não foi qualificado pelo SDR/)).toBeInTheDocument();
    expect(screen.getByText('Passo: Início')).toBeInTheDocument();
    expect(screen.getByText('Qualificação em andamento')).toBeInTheDocument();
    // Nenhum rótulo de campo estruturado é renderizado como se fosse resposta.
    expect(screen.queryByText('Tipo de demanda')).not.toBeInTheDocument();
    expect(screen.queryByText('Empregador')).not.toBeInTheDocument();
  });

  it('lida com o payload mascarado sem quebrar e avisa que está mascarado', async () => {
    vi.spyOn(klmApi, 'sdrStatus').mockResolvedValue(maskedStatus());

    renderPanel({ canViewSensitiveLeadData: false });

    expect(await screen.findByText(/Leitura mascarada por permissão/)).toBeInTheDocument();
    // Campos preservados pelo mascaramento continuam visíveis.
    expect(screen.getByText('Lead 555555')).toBeInTheDocument();
    expect(screen.getByText('Rescisão indireta')).toBeInTheDocument();
    // Campos zerados pelo mascaramento não viram linha vazia.
    expect(screen.queryByText('Telefone')).not.toBeInTheDocument();
    expect(screen.queryByText('Empregador')).not.toBeInTheDocument();
    // Textos protegidos aparecem como o servidor os devolveu, sem inventar conteúdo.
    expect(screen.getAllByText('[conteúdo protegido]').length).toBeGreaterThan(0);
  });

  it('desabilita "Requalificar" sem a permissão kanban.edit e dispara o reprocess com ela', async () => {
    vi.spyOn(klmApi, 'sdrStatus').mockResolvedValue({
      matter_id: MATTER_ID,
      sdr_step: 'coletando_documentos',
      sdr_completed: false,
      qualification: fullQualification(),
    });
    const reprocess = vi.spyOn(klmApi, 'reprocessSdr').mockResolvedValue({
      matter_id: MATTER_ID,
      status: 'queued',
      message: 'Reprocessamento do SDR iniciado em background.',
    });

    const { unmount } = renderPanel({ canReprocess: false });
    const blocked = await screen.findByRole('button', { name: /Requalificar/ });
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveAttribute('title', 'Permissão kanban.edit necessária');
    fireEvent.click(blocked);
    expect(reprocess).not.toHaveBeenCalled();
    unmount();

    renderPanel({ canReprocess: true });
    const allowed = await screen.findByRole('button', { name: /Requalificar/ });
    expect(allowed).toBeEnabled();
    fireEvent.click(allowed);
    await waitFor(() => expect(reprocess).toHaveBeenCalledWith(MATTER_ID, expect.any(String)));
  });
});
