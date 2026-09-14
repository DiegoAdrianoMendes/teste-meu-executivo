import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FlowCopilot } from '@/klm/components/FlowCopilot';
import { klmApi, type KlmFlowCopilotPlan } from '@/klm/api';

vi.mock('@/klm/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/klm/api')>();
  return {
    ...original,
    klmApi: {
      ...original.klmApi,
      flowCopilotPlan: vi.fn(),
    },
  };
});

const plan: KlmFlowCopilotPlan = {
  proposal_id: '4d5e4b93-bf51-4b5f-9f22-b751b989e0dc',
  status: 'awaiting_approval',
  engine: 'klm_flow_copilot_rules_v1',
  summary: 'Proposta com 3 novas etapas para qualificar e pedir documentos.',
  rationale: ['O desenho atual foi preservado.'],
  suggestions: ['Teste o caminho feliz e uma falha antes de publicar.'],
  questions: ['Em qual etapa uma pessoa deve revisar?'],
  warnings: ['A proposta não foi salva nem executada.'],
  nodes: [
    {
      id: 'trigger-1',
      type: 'trigger',
      label: 'Novo lead',
      position: { x: 10, y: 10 },
      config: { event: 'new_lead' },
    },
  ],
  edges: [],
  added_node_ids: ['trigger-1'],
  playbook_name: 'Atendimento jurídico',
  playbook_version: 3,
  account_context_used: true,
  attachment_count: 0,
  approval_required: true,
  external_actions_executed: 0,
  generated_at: '2026-07-24T12:00:00Z',
};

function renderCopilot(onApply = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <FlowCopilot
        flowId="5c00d48f-c9b4-49ce-b7ef-acde1d319172"
        flowName="Triagem"
        practiceArea="saude"
        workspaceId="workspace-1"
        userId="user-1"
        currentNodes={[]}
        currentEdges={[]}
        onApply={onApply}
      />
    </QueryClientProvider>,
  );
  return onApply;
}

describe('FlowCopilot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('propõe, pede aprovação e aplica somente após confirmação', async () => {
    vi.mocked(klmApi.flowCopilotPlan).mockResolvedValue(plan);
    const onApply = renderCopilot();

    fireEvent.click(screen.getByRole('button', { name: 'Abrir copiloto de flows' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Pedido para o copiloto de flows' }), {
      target: { value: 'Crie a triagem e peça documentos.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar pedido ao copiloto' }));

    await waitFor(() => expect(klmApi.flowCopilotPlan).toHaveBeenCalledWith(expect.objectContaining({
      instruction: 'Crie a triagem e peça documentos.',
      flow_id: '5c00d48f-c9b4-49ce-b7ef-acde1d319172',
      practice_area: 'saude',
    })));
    expect(await screen.findByText('Proposta pronta para revisão')).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Aprovar e aplicar' }));
    expect(onApply).toHaveBeenCalledWith(plan);
    expect(
      window.localStorage.getItem(
        'meu-executivo:flow-copilot:workspace-1:user-1:5c00d48f-c9b4-49ce-b7ef-acde1d319172',
      ),
    ).not.toBeNull();
  });

  it('bloqueia áudio antes de chamar o backend', async () => {
    renderCopilot();
    fireEvent.click(screen.getByRole('button', { name: 'Abrir copiloto de flows' }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const audio = new File(['voice'], 'pedido.ogg', { type: 'audio/ogg' });

    fireEvent.change(input, { target: { files: [audio] } });

    expect(await screen.findByText(/pedido\.ogg: áudio não é aceito/i)).toBeInTheDocument();
    expect(klmApi.flowCopilotPlan).not.toHaveBeenCalled();
  });

  it('descarta uma proposta antiga quando o canvas mudou', async () => {
    vi.mocked(klmApi.flowCopilotPlan).mockResolvedValue(plan);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const onApply = vi.fn();
    const renderView = (nodes: Array<Record<string, unknown>>) => (
      <QueryClientProvider client={queryClient}>
        <FlowCopilot
          flowId="5c00d48f-c9b4-49ce-b7ef-acde1d319172"
          flowName="Triagem"
          practiceArea="saude"
          workspaceId="workspace-1"
          userId="user-1"
          currentNodes={nodes}
          currentEdges={[]}
          onApply={onApply}
        />
      </QueryClientProvider>
    );
    const view = render(renderView([]));

    fireEvent.click(screen.getByRole('button', { name: 'Abrir copiloto de flows' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Pedido para o copiloto de flows' }), {
      target: { value: 'Crie uma triagem completa.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enviar pedido ao copiloto' }));
    expect(await screen.findByText('Proposta pronta para revisão')).toBeInTheDocument();

    view.rerender(renderView([
      {
        id: 'manual-edit',
        type: 'trigger',
        config: { event: 'manual' },
      },
    ]));
    fireEvent.click(screen.getByRole('button', { name: 'Aprovar e aplicar' }));

    expect(onApply).not.toHaveBeenCalled();
    expect(await screen.findByText(/canvas mudou desde esta proposta/i)).toBeInTheDocument();
  });
});
