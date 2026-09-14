/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConversationCopilotPanel } from '@/klm/components/ConversationCopilotPanel';
import { klmApi, type KlmOmnichannelCopilot } from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const copilotResult: KlmOmnichannelCopilot = {
  action: 'suggest_reply',
  engine: 'native-evidence-v6',
  content: 'Entendi. Para avaliarmos a negativa, você consegue enviar a prescrição e a resposta do plano?',
  suggestions: ['Envie a prescrição e a negativa do plano, por favor.'],
  unansweredQuestions: ['Qual é o medicamento prescrito?'],
  tasks: [{ title: 'Coletar prescrição médica', kind: 'document', source: 'conversation' }],
  evidenceMessageIds: ['message-1', 'message-2'],
  confidence: 0.92,
  requiresModel: false,
  externalRequests: 0,
  generatedAt: '2026-07-20T12:00:00Z',
};

function renderPanel(
  onUseResponse = vi.fn(),
  draft = '',
  options: { onOpenSchedule?: () => void; canCreateTasks?: boolean } = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ConversationCopilotPanel
        threadId="thread-canonical"
        matterId="matter-canonical"
        canEdit
        canCreateTasks={options.canCreateTasks ?? true}
        draft={draft}
        onUseResponse={onUseResponse}
        onOpenSchedule={options.onOpenSchedule}
      />
    </QueryClientProvider>,
  );
  return onUseResponse;
}

describe('ConversationCopilotPanel', () => {
  it('generates an evidence-bound reply and places it in the response field only after a click', async () => {
    const request = vi.spyOn(klmApi, 'omnichannelCopilot').mockResolvedValue(copilotResult);
    const onUseResponse = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Objetivo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Responder' }));

    await waitFor(() => expect(request).toHaveBeenCalledWith('thread-canonical', expect.objectContaining({
      action: 'suggest_reply',
      tone: 'objetivo',
    })));
    expect(await screen.findByDisplayValue(copilotResult.content)).toBeInTheDocument();
    expect(screen.getByText('Qual é o medicamento prescrito?')).toBeInTheDocument();
    expect(screen.getByText('Coletar prescrição médica')).toBeInTheDocument();
    expect(screen.getByText(/0 chamada\(s\) externa\(s\)/)).toBeInTheDocument();
    expect(onUseResponse).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Colocar no campo de resposta' }));
    expect(onUseResponse).toHaveBeenCalledWith(copilotResult.content);
  });

  it('rewrites the operator draft without sending it automatically', async () => {
    const rewritten = { ...copilotResult, action: 'rewrite', content: 'Você poderia encaminhar a prescrição e a negativa do plano, por gentileza?' };
    const request = vi.spyOn(klmApi, 'omnichannelCopilot').mockResolvedValue(rewritten);
    const onUseResponse = renderPanel(vi.fn(), 'manda os documento');

    fireEvent.click(screen.getByRole('button', { name: 'Reescrever meu rascunho' }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('thread-canonical', expect.objectContaining({
      action: 'rewrite',
      draft: 'manda os documento',
    })));
    expect(onUseResponse).not.toHaveBeenCalled();
  });

  it('creates an idempotent task on the linked card and disables the repeated click', async () => {
    vi.spyOn(klmApi, 'omnichannelCopilot').mockResolvedValue(copilotResult);
    const createTask = vi.spyOn(klmApi, 'createMatterTask').mockResolvedValue({
      id: 'task-created',
      version: 1,
      matterId: 'matter-canonical',
      title: 'Coletar prescrição médica',
      description: 'Criada pelo Assistente KLM.',
      status: 'pending',
      priority: 'alta',
      assignedTo: null,
      dueDate: null,
      isAiGenerated: true,
      createdAt: '2026-07-20T12:00:00Z',
    });
    renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Tarefas' }));
    const createButton = await screen.findByRole('button', {
      name: 'Criar tarefa: Coletar prescrição médica',
    });
    fireEvent.click(createButton);

    await waitFor(() => expect(createTask).toHaveBeenCalledWith(
      'matter-canonical',
      expect.objectContaining({
        title: 'Coletar prescrição médica',
        isAiGenerated: true,
        idempotencyKey: expect.stringMatching(/^copilot-task:thread-canonical:/),
      }),
    ));
    const completedButton = await screen.findByRole('button', {
      name: 'Tarefa criada: Coletar prescrição médica',
    });
    expect(completedButton).toBeDisabled();
    fireEvent.click(completedButton);
    expect(createTask).toHaveBeenCalledTimes(1);
  });

  it('turns unanswered questions and meeting suggestions into explicit one-click actions', async () => {
    vi.spyOn(klmApi, 'omnichannelCopilot').mockResolvedValue({
      ...copilotResult,
      action: 'meeting_brief',
      tasks: [{ title: 'Definir reunião on-line', kind: 'meeting', source: 'conversation' }],
    });
    const onUseResponse = vi.fn();
    const onOpenSchedule = vi.fn();
    renderPanel(onUseResponse, '', { onOpenSchedule });

    fireEvent.click(screen.getByRole('button', { name: 'Reunião' }));
    const prepare = await screen.findByRole('button', {
      name: 'Preparar resposta para: Qual é o medicamento prescrito?',
    });
    fireEvent.click(prepare);
    expect(onUseResponse).toHaveBeenCalledWith(expect.stringContaining('Qual é o medicamento prescrito?'));

    fireEvent.click(screen.getByRole('button', { name: 'Abrir agenda e disponibilidades' }));
    expect(onOpenSchedule).toHaveBeenCalledTimes(1);
  });
});
