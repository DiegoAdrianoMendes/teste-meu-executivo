import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

import { NodeConfigPanel } from '@/components/flow/NodeConfigPanel';
import { humanScheduleIsoToLocal, humanScheduleLocalToIso } from '@/lib/flow-human-schedule-time';
import { klmApi } from '@/klm/api';

function renderWithQuery(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);
}

const conditionCatalogNode = {
  type: 'condition',
  label: 'Condição do runtime',
  description: 'Contrato devolvido pelo runtime.',
  category: 'Controle',
  execution_mode: 'control' as const,
  side_effect: 'none' as const,
  input_ports: [{ key: 'input', label: 'Entrada' }],
  output_ports: [{ key: 'true', label: 'Sim' }, { key: 'false', label: 'Não' }],
  supports_dry_run: true,
  supports_error_route: false,
  config_version: 2,
  schema: {
    type: 'object',
    properties: {
      field: { type: 'string', title: 'Campo do catálogo' },
    },
  },
  uischema: {
    type: 'VerticalLayout',
    elements: [{ type: 'Control', scope: '#/properties/field' }],
  },
};

describe('NodeConfigPanel catalog integration', () => {
  it('prefere o formulário publicado pelo runtime ao editor manual', () => {
    render(
      <NodeConfigPanel
        node={{
          id: 'condition-1',
          type: 'condition',
          position: { x: 0, y: 0 },
          data: { label: 'Minha condição', field: 'lead.status' },
        }}
        catalogNode={conditionCatalogNode}
        onClose={() => {}}
        onUpdate={vi.fn()}
      />,
    );

    expect(screen.getByTestId('schema-driven-node-panel')).toBeInTheDocument();
    expect(screen.getByLabelText('Campo do catálogo')).toHaveValue('lead.status');
    expect(screen.getByText('Condição do runtime')).toBeInTheDocument();
  });

  it('não finge um formulário para capacidade sem contrato do backend', () => {
    render(
      <NodeConfigPanel
        node={{
          id: 'human-notify-1',
          type: 'human_notify',
          position: { x: 0, y: 0 },
          data: { label: 'Notificar humano' },
        }}
        catalogNode={{ ...conditionCatalogNode, type: 'human_notify', label: 'Notificar humano', schema: undefined, uischema: undefined }}
        onClose={() => {}}
        onUpdate={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('schema-driven-node-panel')).not.toBeInTheDocument();
    expect(screen.getByText(/ainda não publicou um contrato de formulário/i)).toBeInTheDocument();
  });

  it('configura agendamento humano com seletor de caso, nunca UUID digitável', async () => {
    vi.spyOn(klmApi, 'funnel').mockResolvedValue({ cards: [{ id: 'matter-1', clientId: 'client-1', name: 'Ana', subject: 'Contrato', object: '', source: '', owner: '', probability: 0, initialFee: 0, projectedRevenue: 0, slaDueAt: null, documents: 0, documentScore: 0, stage: 'novo', microStatus: 'novo' }] } as Awaited<ReturnType<typeof klmApi.funnel>>);
    renderWithQuery(<NodeConfigPanel node={{ id: 'schedule-1', type: 'human_schedule', position: { x: 0, y: 0 }, data: { label: 'Agendar' } }} catalogNode={{ ...conditionCatalogNode, type: 'human_schedule', label: 'Agendar', schema: { type: 'object' } }} onClose={() => {}} onUpdate={vi.fn()} />);
    expect(screen.getByTestId('human-node-config-panel')).toBeInTheDocument();
    expect(screen.getByText(/fluxo fica pausado/i)).toBeInTheDocument();
    expect(screen.getByText('Caso vinculado')).toBeInTheDocument();
    expect(screen.queryByText(/matter_id/i)).not.toBeInTheDocument();
  });

  it('persiste horários de human_schedule com timezone explícito e hidrata o datetime-local', () => {
    const onUpdate = vi.fn();
    const persistedStart = '2026-08-03T17:30:00.000Z';
    renderWithQuery(<NodeConfigPanel node={{ id: 'schedule-timezone', type: 'human_schedule', position: { x: 0, y: 0 }, data: { label: 'Agendar', starts_at: persistedStart } }} catalogNode={{ ...conditionCatalogNode, type: 'human_schedule', label: 'Agendar', schema: { type: 'object' } }} onClose={() => {}} onUpdate={onUpdate} />);
    expect(screen.getByLabelText('Início do compromisso')).toHaveValue(humanScheduleIsoToLocal(persistedStart));

    fireEvent.input(screen.getByLabelText('Início do compromisso'), { target: { value: '2026-08-03T15:30' } });
    expect(onUpdate).toHaveBeenCalled();
    const saved = onUpdate.mock.calls.find(([nodeId]) => nodeId === 'schedule-timezone')?.[1]?.starts_at;
    expect(saved).toBe(humanScheduleLocalToIso('2026-08-03T15:30'));
    // MeetingCreate rejects timezone-naive values; Z is a valid explicit UTC offset.
    expect(saved).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    expect(humanScheduleLocalToIso('2026-02-30T14:30')).toBeNull();
  });

  it('configura escalonamento por checkpoint do fluxo e não por id técnico', () => {
    renderWithQuery(<NodeConfigPanel node={{ id: 'escalate-1', type: 'human_escalate', position: { x: 0, y: 0 }, data: { label: 'Escalonar' } }} catalogNode={{ ...conditionCatalogNode, type: 'human_escalate', label: 'Escalonar', schema: { type: 'object' } }} humanCheckpointTargets={[{ id: 'human-review-1', label: 'Revisar minuta' }]} onClose={() => {}} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByRole('combobox'));
    expect(screen.getByText('Revisar minuta')).toBeInTheDocument();
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('configura pedido de informação humana com funções, SLA e campos guiados', () => {
    const onUpdate = vi.fn();
    renderWithQuery(<NodeConfigPanel node={{ id: 'input-1', type: 'human_request_input', position: { x: 0, y: 0 }, data: { label: 'Pedir documento' } }} catalogNode={{ ...conditionCatalogNode, type: 'human_request_input', label: 'Pedir informação', schema: { type: 'object' } }} onClose={() => {}} onUpdate={onUpdate} />);
    expect(screen.getByTestId('human-node-config-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Gestão jurídica' }));
    fireEvent.keyDown(screen.getByLabelText('Novo campo solicitado'), { key: 'Enter', target: { value: 'documento_comprovante' } });
    expect(onUpdate).toHaveBeenCalledWith('input-1', expect.objectContaining({ required_roles: expect.arrayContaining(['legal_manager']) }));
    expect(onUpdate).toHaveBeenCalledWith('input-1', expect.objectContaining({ options: expect.objectContaining({ inputFields: ['documento_comprovante'] }) }));
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it.each(['human_review', 'human_approval', 'human_wait'] as const)('configura %s com rota humana finita sem fallback técnico', (type) => {
    renderWithQuery(<NodeConfigPanel node={{ id: `${type}-1`, type, position: { x: 0, y: 0 }, data: { label: type } }} catalogNode={{ ...conditionCatalogNode, type, label: type, schema: { type: 'object' } }} onClose={() => {}} onUpdate={vi.fn()} />);
    expect(screen.getByTestId('human-node-config-panel')).toBeInTheDocument();
    expect(screen.getByText('Máximo de escaladas')).toBeInTheDocument();
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('configura human_resolve por checkpoint selecionável, nunca ID bruto', () => {
    renderWithQuery(<NodeConfigPanel node={{ id: 'resolve-1', type: 'human_resolve', position: { x: 0, y: 0 }, data: { label: 'Resolver' } }} catalogNode={{ ...conditionCatalogNode, type: 'human_resolve', label: 'Resolver', schema: { type: 'object' } }} humanCheckpointTargets={[{ id: 'review-1', label: 'Revisão jurídica pendente' }]} onClose={() => {}} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByRole('combobox'));
    expect(screen.getByText('Revisão jurídica pendente')).toBeInTheDocument();
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('configura consulta financeira com seletores do workspace, sem UUID', async () => {
    vi.spyOn(klmApi, 'listFinanceCostCenters').mockResolvedValue([{ id: 'cc-1', workspaceId: 'ws', code: 'ADM', name: 'Administrativo', active: true, version: 1, type: 'operational', status: 'active' }]);
    vi.spyOn(klmApi, 'financeLookup').mockResolvedValue({ items: [{ id: 'client-1', label: 'Cliente 360' }], entityType: 'client', search: '', limit: 100, nextCursor: null });
    renderWithQuery(<NodeConfigPanel node={{ id: 'finance-read-1', type: 'finance_read', position: { x: 0, y: 0 }, data: { label: 'Financeiro', operation: 'drilldown' } }} catalogNode={{ ...conditionCatalogNode, type: 'finance_read', label: 'Consultar financeiro', schema: { type: 'object' } }} onClose={() => {}} onUpdate={vi.fn()} />);
    expect(screen.getByTestId('finance-node-config-panel')).toBeInTheDocument();
    expect(screen.getByText('Contexto financeiro')).toBeInTheDocument();
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });

  it('configura rascunho financeiro com revisão humana obrigatória', async () => {
    vi.spyOn(klmApi, 'listFinanceCostCenters').mockResolvedValue([]);
    vi.spyOn(klmApi, 'financeLookup').mockResolvedValue({ items: [], entityType: 'client', search: '', limit: 100, nextCursor: null });
    renderWithQuery(<NodeConfigPanel node={{ id: 'finance-draft-1', type: 'finance_draft', position: { x: 0, y: 0 }, data: { label: 'Rascunho financeiro' } }} catalogNode={{ ...conditionCatalogNode, type: 'finance_draft', label: 'Rascunho financeiro', schema: { type: 'object' } }} onClose={() => {}} onUpdate={vi.fn()} />);
    expect(screen.getByTestId('finance-node-config-panel')).toBeInTheDocument();
    expect(screen.getByLabelText('Valor do rascunho financeiro')).toBeInTheDocument();
    expect(screen.getByText('Revisão humana obrigatória')).toBeInTheDocument();
    expect(screen.queryByLabelText(/UUID/i)).not.toBeInTheDocument();
  });
});
