import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  klmApi,
  type KlmOperationalCopilotPlan,
} from '@/klm/api';
import { OperationalCopilot } from '@/klm/components/OperationalCopilot';

vi.mock('@/klm/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/klm/api')>();
  return {
    ...original,
    klmApi: {
      ...original.klmApi,
      operationalCopilotPlan: vi.fn(),
    },
  };
});

const plan: KlmOperationalCopilotPlan = {
  proposal_id: '2758f4a2-0c30-4792-95f2-699fa70e9a37',
  status: 'awaiting_approval',
  engine: 'meu-executivo-operational-rules-v1',
  domain: 'agenda',
  summary: '1 ação preparada em Agenda.',
  rationale: ['A proposta usa somente ações tipadas.'],
  suggestions: ['Revise conflitos antes de confirmar.'],
  questions: [],
  warnings: ['Nada foi criado.'],
  actions: [
    {
      action_id: '8471dc5d-0cd8-49b8-a5c8-dd6d6e76c0aa',
      label: 'Preparar reunião',
      description: 'Abre um rascunho local.',
      kind: 'local_draft',
      risk: 'medium',
      payload: {
        type: 'agenda.open_create',
        date: '2026-07-25',
        hour: 11,
        minute: 0,
      },
      required_permission: 'kanban:edit',
      approval_required: true,
    },
  ],
  playbook_name: null,
  playbook_version: null,
  account_context_used: true,
  attachment_count: 0,
  approval_required: true,
  external_actions_executed: 0,
  generated_at: '2026-07-24T12:00:00Z',
};

function renderCopilot({
  contextSnapshot = { selected_date: '2026-07-24' },
  onApply = vi.fn(),
}: {
  contextSnapshot?: Record<string, unknown>;
  onApply?: ReturnType<typeof vi.fn>;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <OperationalCopilot
        domain="agenda"
        title="Copiloto da Agenda"
        subtitle="Agenda local"
        welcome="Descreva o compromisso que deseja preparar."
        placeholder="Ex.: prepare uma reunião amanhã às 11h."
        contextKey="agenda"
        contextSnapshot={contextSnapshot}
        workspaceId="workspace-1"
        userId="user-1"
        onApply={onApply}
      />
    </QueryClientProvider>,
  );
  return { onApply, queryClient, view };
}

describe('OperationalCopilot', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('propõe e só aplica depois da aprovação explícita', async () => {
    vi.mocked(klmApi.operationalCopilotPlan).mockResolvedValue(plan);
    const { onApply } = renderCopilot();

    fireEvent.click(
      screen.getByRole('button', { name: 'Abrir copiloto da agenda' }),
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Pedido para copiloto da agenda' }),
      { target: { value: 'Prepare uma reunião amanhã às 11h.' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Enviar pedido ao copiloto' }),
    );

    await waitFor(() => expect(
      klmApi.operationalCopilotPlan,
    ).toHaveBeenCalledWith(expect.objectContaining({
      domain: 'agenda',
      instruction: 'Prepare uma reunião amanhã às 11h.',
      context_key: 'agenda',
      context_snapshot: { selected_date: '2026-07-24' },
    })));
    expect(await screen.findByText('Proposta pronta para revisão')).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Aprovar e aplicar' }));

    await waitFor(() => expect(onApply).toHaveBeenCalledWith(plan.actions, plan));
    expect(
      window.localStorage.getItem(
        'meu-executivo:operational-copilot:workspace-1:user-1:agenda:agenda',
      ),
    ).not.toBeNull();
  });

  it('bloqueia áudio antes de chamar o backend', async () => {
    renderCopilot();
    fireEvent.click(
      screen.getByRole('button', { name: 'Abrir copiloto da agenda' }),
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const audio = new File(['voice'], 'pedido.ogg', { type: 'audio/ogg' });

    fireEvent.change(input, { target: { files: [audio] } });

    expect(await screen.findByText(/pedido\.ogg: áudio não é aceito/i)).toBeInTheDocument();
    expect(klmApi.operationalCopilotPlan).not.toHaveBeenCalled();
  });

  it('remove segredos aninhados do contexto enviado e persistido', async () => {
    vi.mocked(klmApi.operationalCopilotPlan).mockResolvedValue(plan);
    renderCopilot({
      contextSnapshot: {
        channel: 'whatsapp',
        connection: {
          status: 'connected',
          accessToken: 'never-send-this',
          nested: { client_secret: 'never-store-this' },
        },
      },
    });

    fireEvent.click(
      screen.getByRole('button', { name: 'Abrir copiloto da agenda' }),
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Pedido para copiloto da agenda' }),
      { target: { value: 'Atualize a agenda local.' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Enviar pedido ao copiloto' }),
    );

    await waitFor(() => expect(
      klmApi.operationalCopilotPlan,
    ).toHaveBeenCalledWith(expect.objectContaining({
      context_snapshot: {
        channel: 'whatsapp',
        connection: {
          status: 'connected',
          nested: {},
        },
      },
    })));
    const stored = window.localStorage.getItem(
      'meu-executivo:operational-copilot:workspace-1:user-1:agenda:agenda',
    );
    expect(stored).not.toContain('never-send-this');
    expect(stored).not.toContain('never-store-this');
  });

  it('descarta a proposta se o contexto da página mudou', async () => {
    vi.mocked(klmApi.operationalCopilotPlan).mockResolvedValue(plan);
    const firstContext = { selected_date: '2026-07-24' };
    const { onApply, queryClient, view } = renderCopilot({
      contextSnapshot: firstContext,
    });

    fireEvent.click(
      screen.getByRole('button', { name: 'Abrir copiloto da agenda' }),
    );
    fireEvent.change(
      screen.getByRole('textbox', { name: 'Pedido para copiloto da agenda' }),
      { target: { value: 'Prepare uma reunião amanhã às 11h.' } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Enviar pedido ao copiloto' }),
    );
    expect(await screen.findByText('Proposta pronta para revisão')).toBeInTheDocument();

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <OperationalCopilot
          domain="agenda"
          title="Copiloto da Agenda"
          subtitle="Agenda local"
          welcome="Descreva o compromisso que deseja preparar."
          placeholder="Ex.: prepare uma reunião amanhã às 11h."
          contextKey="agenda"
          contextSnapshot={{ selected_date: '2026-07-26' }}
          workspaceId="workspace-1"
          userId="user-1"
          onApply={onApply}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Aprovar e aplicar' }));

    expect(onApply).not.toHaveBeenCalled();
    expect(await screen.findByText(/A página mudou desde esta proposta/i)).toBeInTheDocument();
  });
});
