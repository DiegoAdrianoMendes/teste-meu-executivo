import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskWorkflowSection } from '@/klm/components/TaskWorkflowSection';
import * as taskWorkflows from '@/services/task-workflows';
import type { CollaborativeTask } from '@/services/tasks';

const { flows } = vi.hoisted(() => ({ flows: vi.fn() }));
vi.mock('@/klm/api', () => ({ klmApi: { flows } }));
vi.mock('@/services/task-workflows');
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

const task = { id: 'task-internal-id', matterId: 'matter-internal-id' } as CollaborativeTask;

function renderSection() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TaskWorkflowSection task={task} canManage />
    </QueryClientProvider>,
  );
}

describe('TaskWorkflowSection', () => {
  beforeEach(() => {
    flows.mockResolvedValue([{ id: 'flow-internal-id', name: 'Preparar contestação', is_active: true, is_template: false }]);
    vi.mocked(taskWorkflows.listTaskWorkflowRuns).mockResolvedValue([]);
    vi.mocked(taskWorkflows.applyTaskWorkflow).mockResolvedValue({ flowName: 'Preparar contestação' } as taskWorkflows.TaskWorkflowRun);
  });

  it('seleciona e aplica workflow por nome sem expor UUID', async () => {
    renderSection();
    fireEvent.click(await screen.findByLabelText('Selecionar workflow'));
    fireEvent.click(await screen.findByText('Preparar contestação'));
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar' }));

    await waitFor(() => expect(taskWorkflows.applyTaskWorkflow).toHaveBeenCalled());
    expect(screen.queryByText('flow-internal-id')).not.toBeInTheDocument();
    expect(screen.queryByText('task-internal-id')).not.toBeInTheDocument();
  });
});
