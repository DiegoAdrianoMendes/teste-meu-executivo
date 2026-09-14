/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TaskLegalResourcesSection } from '@/klm/components/TaskLegalResourcesSection';
import * as tasks from '@/services/tasks';
import type { CollaborativeTask } from '@/services/tasks';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('seletor jurídico de dependências', () => {
  it('consulta apenas candidatas autorizadas do contrato dedicado', async () => {
    vi.spyOn(tasks, 'getTaskLegalResources').mockResolvedValue({
      labels: [],
      subtasks: [],
      dependencies: [],
      attachments: [],
      mentions: [],
    });
    const candidates = vi.spyOn(tasks, 'listTaskDependencyCandidates').mockResolvedValue([
      {
        handle: 'opaque-candidate',
        label: 'Revisar prova documental',
        status: 'pending',
        dueAt: null,
      },
    ]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const task = {
      id: 'task-current',
      version: 3,
      matterId: 'matter-internal-id-not-used-by-selector',
    } as CollaborativeTask;

    render(
      <QueryClientProvider client={queryClient}>
        <TaskLegalResourcesSection task={task} canEdit onConflict={vi.fn()} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(candidates).toHaveBeenCalledWith('task-current'));
    await waitFor(() => expect(screen.getByText('Esta tarefa depende de')).toBeInTheDocument());
    expect(screen.getByText('Selecione uma tarefa do mesmo contexto')).toBeInTheDocument();
  });
});
