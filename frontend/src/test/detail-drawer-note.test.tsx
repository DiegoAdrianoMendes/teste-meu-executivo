/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DetailDrawer } from '@/components/layout/DetailDrawer';
import type { MatterCard } from '@/types';

const storeMocks = vi.hoisted(() => ({
  addNote: vi.fn(),
  can: vi.fn((_module: string, _action?: string) => true),
  closeDrawer: vi.fn(),
  toggleTask: vi.fn().mockResolvedValue(undefined),
  updateCardField: vi.fn().mockResolvedValue(undefined),
  overrideRun: vi.fn(),
  retryRun: vi.fn(),
}));
const toastMock = vi.hoisted(() => vi.fn());

const card = {
  id: 'matter-1',
  client: { id: 'client-1', name: 'Maria Silva', phone: '+5561999990000', email: 'maria@example.com' },
  currentAgent: 'klm_sdr',
  demandType: 'Negativa de cobertura',
  columnId: 'triagem_sdr',
  microStatus: 'qualificado',
  priority: 'media',
  urgency: 'normal',
  classification: 'Bom',
  owner: 'Advogado A',
  responsibleHuman: 'Advogado A',
  tags: [],
  nextAction: 'Ligar para a cliente',
  nextDeadline: null,
  caseNumber: null,
  claimValue: null,
  leadSource: null,
  docs: 0,
  docScore: 0,
  documentScore: 0,
  productionScore: 0,
  starsRating: 0,
  isBlocked: false,
  needsApproval: false,
  executiveSummary: 'Resumo executivo do caso.',
  strategyNotes: [],
  risks: [],
  blocks: [],
  tasks: [{
    id: 'task-1',
    title: 'Ligar para a cliente',
    status: 'pending',
    assignedTo: 'user-1',
    createdAt: '2026-09-04T12:00:00Z',
    isAiGenerated: false,
    version: 2,
  }],
  openTasks: 1,
  timeline: [],
  documents: [],
  conversations: [],
  agentRuns: [],
  humanOverrides: [],
  fieldChanges: [],
  knowledgeChunks: [],
  insights: [],
  auditLogs: [],
  cardScore: null,
  revenueProjection: null,
  honorariumEstimate: null,
  campaignAttribution: null,
} as unknown as MatterCard;

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    state: { drawerOpen: true, selectedCardId: 'matter-1', cards: [card] },
    getSelectedCard: () => card,
    ...storeMocks,
  }),
}));
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }));

async function openTasksTab() {
  // Radix Tabs ativa no mousedown (e no foco), não no click sintético.
  const tab = await screen.findByRole('tab', { name: /tarefas/i });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.focus(tab);
  fireEvent.click(tab);
}

function renderDrawer() {
  return render(
    <MemoryRouter>
      <DetailDrawer />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  storeMocks.can.mockImplementation(() => true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('DetailDrawer: observação rápida persiste no backend', () => {
  it('chama addNote, limpa o campo só após sucesso e mantém o texto quando falha', async () => {
    let rejectNote: (error: Error) => void = () => undefined;
    storeMocks.addNote.mockImplementationOnce(() => new Promise<void>((_, reject) => { rejectNote = reject; }));
    renderDrawer();

    const input = await screen.findByRole('textbox', { name: 'Observação rápida' });
    fireEvent.change(input, { target: { value: 'Cliente confirmou os documentos' } });
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }));

    expect(storeMocks.addNote).toHaveBeenCalledWith('matter-1', 'Cliente confirmou os documentos');
    expect(screen.getByRole('button', { name: 'Salvando...' })).toBeDisabled();

    rejectNote(new Error('backend indisponível'));

    await waitFor(() => expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'A observação não foi salva.',
      description: 'backend indisponível',
      variant: 'destructive',
    })));
    expect(input).toHaveValue('Cliente confirmou os documentos');

    storeMocks.addNote.mockResolvedValueOnce(undefined);
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(input).toHaveValue(''));
    expect(storeMocks.addNote).toHaveBeenCalledTimes(2);
  });

  it('conclui a tarefa pelo store e avisa quando a API falha (V9-b)', async () => {
    storeMocks.toggleTask.mockRejectedValueOnce(new Error('A tarefa mudou em outro lugar'));
    renderDrawer();

    await openTasksTab();
    const checkbox = await screen.findByRole('checkbox', { name: 'Concluir tarefa Ligar para a cliente' });
    expect(checkbox).toBeEnabled();
    fireEvent.click(checkbox);

    expect(storeMocks.toggleTask).toHaveBeenCalledWith('matter-1', 'task-1');
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'A tarefa não foi atualizada.',
      description: 'A tarefa mudou em outro lugar',
      variant: 'destructive',
    })));

    fireEvent.click(checkbox);
    expect(storeMocks.toggleTask).toHaveBeenCalledTimes(2);
    await act(async () => {
      await Promise.resolve();
    });
    expect(toastMock).toHaveBeenCalledTimes(1);
  });

  it('bloqueia o campo e o botão sem kanban.edit', async () => {
    storeMocks.can.mockImplementation((module: string, action?: string) =>
      !((module === 'kanban' && action === 'edit') || (module === 'tasks' && action === 'complete')));
    renderDrawer();

    const input = await screen.findByRole('textbox', { name: 'Observação rápida' });
    expect(input).toBeDisabled();
    expect(input).toHaveAttribute('placeholder', 'Sem permissão para anotar neste caso');
    const saveButton = screen.getByRole('button', { name: 'Salvar' });
    expect(saveButton).toBeDisabled();
    expect(saveButton).toHaveAttribute('title', 'Permissão kanban.edit necessária');
    fireEvent.change(input, { target: { value: 'tentativa sem permissão' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(storeMocks.addNote).not.toHaveBeenCalled();

    await openTasksTab();
    const checkbox = await screen.findByRole('checkbox', { name: 'Concluir tarefa Ligar para a cliente' });
    expect(checkbox).toBeDisabled();
    expect(checkbox).toHaveAttribute('title', 'Permissão tasks.complete necessária');
    fireEvent.click(checkbox);
    expect(storeMocks.toggleTask).not.toHaveBeenCalled();
  });
});
