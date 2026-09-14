/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LayoutDashboard } from 'lucide-react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { CommandPalette } from '@/klm/components/CommandPalette';
import { klmApi } from '@/klm/api';
import type { KlmNavItem } from '@/klm/navigation';
import * as taskService from '@/services/tasks';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const navigationItems: KlmNavItem[] = [
  { label: 'Início jurídico', shortLabel: 'Início', path: '/app', icon: LayoutDashboard, group: 'operacao', permission: { module: 'dashboard', action: 'view' } },
];

function renderLegalPalette() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CommandPalette
          open
          onOpenChange={vi.fn()}
          navigationItems={navigationItems}
          funnelCards={[]}
          threads={[]}
          contextualPath={(path) => path}
          workspaceMode="legal"
          legalSearchPermissions={{ tasks: true, processes: true, demands: true, documents: true }}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('pesquisa jurídica global', () => {
  it('consulta fontes reais somente após dois caracteres e preserva resultados parciais', async () => {
    const taskSearch = vi.spyOn(taskService, 'listCollaborativeTasks').mockResolvedValue({
      items: [{
        id: 'task-1',
        workspaceId: 'workspace-1',
        contextType: 'matter',
        origin: 'manual',
        matterId: 'matter-1',
        clientId: null,
        contractId: null,
        originResourceId: null,
        originLabel: null,
        parentTaskId: null,
        title: 'Revisar processo fiscal',
        description: null,
        status: 'pending',
        priority: 'alta',
        assignedTo: null,
        dueDate: null,
        dueAt: null,
        legacyDueText: null,
        reviewDueAt: null,
        reviewRequired: false,
        reviewState: 'not_required',
        reviewRound: 0,
        version: 1,
        createdByUserId: 'user-1',
        completedAt: null,
        cancelledAt: null,
        cancelledByUserId: null,
        cancellationReason: null,
        participants: [],
        reviewRounds: [],
        isAiGenerated: false,
        createdAt: '2026-08-12T00:00:00Z',
        sourceExceptionId: null,
        matterLabel: 'Caso tributário',
      }],
      total: 1,
      nextCursor: null,
      hasMore: false,
    });
    vi.spyOn(klmApi, 'listMonitoredProcesses').mockResolvedValue([{
      id: 'process-1', workspaceId: 'workspace-1', matterId: 'matter-1', numeroCnj: '0001234-56.2026.8.07.0001', tribunal: 'TJDFT', status: 'ativo', oabVinculada: null, responsibleUserId: null, createdByUserId: 'user-1', lastMovementAt: null, lastError: null, createdAt: '2026-08-12T00:00:00Z', updatedAt: '2026-08-12T00:00:00Z',
    }]);
    vi.spyOn(klmApi, 'listLegalDemands').mockResolvedValue({
      total: 1,
      items: [{
        id: 'demand-1', workspaceId: 'workspace-1', externalRef: 'Processo fiscal estratégico', nature: 'judicial', area: 'Tributário', subarea: null, clientId: 'client-1', parties: [], unit: null, team: null, responsibleUserId: null, strategy: null, risk: 'alto', value: null, phase: 'em_andamento', sla: {}, playbookRef: null, documentsRequired: [], matterId: 'matter-1', contractRef: null, financeRef: null, createdAt: '2026-08-12T00:00:00Z', updatedAt: '2026-08-12T00:00:00Z',
      }],
    });
    vi.spyOn(klmApi, 'listLegalDocuments').mockResolvedValue([{
      id: 'document-1', storageObjectId: 'storage-1', contentSha256: 'hash', originalFilename: 'processo-fiscal.pdf', matterId: 'matter-1', clientId: 'client-1', documentType: 'peticao_inicial', classificationMethod: 'manual', classificationConfidence: 1, ocrStatus: 'concluido', createdAt: '2026-08-12T00:00:00Z', storage: null,
    }]);

    renderLegalPalette();
    const input = screen.getByLabelText('Pesquisa jurídica global');
    fireEvent.change(input, { target: { value: 'p' } });
    expect(taskSearch).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: 'fiscal' } });
    await waitFor(() => expect(screen.getByText('Revisar processo fiscal')).toBeInTheDocument());
    expect(screen.getByText('Processo fiscal estratégico')).toBeInTheDocument();
    expect(screen.getByText('processo-fiscal.pdf')).toBeInTheDocument();
    expect(taskSearch).toHaveBeenCalledWith({ view: 'mine', search: 'fiscal', limit: 8 });

    fireEvent.change(input, { target: { value: '0001234' } });
    await waitFor(() => expect(screen.getByText('0001234-56.2026.8.07.0001')).toBeInTheDocument());
  });
});
