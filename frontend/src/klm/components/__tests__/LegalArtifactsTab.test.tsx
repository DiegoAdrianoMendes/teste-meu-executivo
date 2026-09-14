/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { LegalArtifactsTab } from '@/klm/components/legal/LegalArtifactsTab';
import { klmApi, type KlmLegalArtifactEditorVersion } from '@/klm/api';
import { ApiError } from '@/services/http';

const toastSpy = vi.fn();
vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: toastSpy }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  toastSpy.mockClear();
});

const renderWithClient = (ui: ReactElement) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

const DEMAND_ID = 'demand-1';

const VERSION_FIXTURE: KlmLegalArtifactEditorVersion = {
  id: 'artifact-1',
  workspaceId: 'ws-1',
  matterId: 'matter-1',
  legalDemandId: DEMAND_ID,
  artifactKey: 'key-1',
  version: 1,
  kind: 'petition',
  title: 'Petição inicial — Caso Teste',
  status: 'draft',
  content: {
    sections: [{ id: 'fatos', title: 'Dos Fatos', body: 'Corpo da seção de fatos.' }],
    variables: {},
    checklist: {},
  },
  sourceSnapshot: {},
  createdByUserId: 'user-1',
  authorType: 'human',
  authorAgentKey: null,
  officeTemplateKey: 'peticao_inicial',
  officeTemplateObjectId: 'doc-1',
  officeTemplateObjectVersion: 1,
  protocolabilityState: 'bloqueado',
  approvalItemId: null,
  createdAt: '2026-07-20T12:00:00Z',
  updatedAt: '2026-07-20T12:00:00Z',
};

describe('LegalArtifactsTab (Sprint A UI — aba Peças)', () => {
  it('lista as versões da demanda e mostra o checklist com o estado bloqueado', async () => {
    vi.spyOn(klmApi, 'listLegalArtifactEditorVersions').mockResolvedValue([VERSION_FIXTURE]);
    vi.spyOn(klmApi, 'getLegalArtifactChecklist').mockResolvedValue({
      hasPlaceholder: true,
      missingVariables: ['cliente_nome'],
      hasProcuracao: false,
      hasCustasPagas: null,
      pendingItems: [{ item: 'placeholder', detail: 'Variável obrigatória cliente_nome ausente.' }],
      protocolabilityState: 'bloqueado',
    });

    renderWithClient(<LegalArtifactsTab demandId={DEMAND_ID} />);

    expect(await screen.findByText('Petição inicial — Caso Teste')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('Variável obrigatória cliente_nome ausente.', { exact: false })).toBeInTheDocument();
    });
    const bloqueadoPills = await screen.findAllByText('Bloqueado');
    expect(bloqueadoPills.length).toBeGreaterThan(0);
  });

  it('gerar peça sem modelo timbrado publicado mostra o erro exato do backend e não fecha o diálogo', async () => {
    vi.spyOn(klmApi, 'listLegalArtifactEditorVersions').mockResolvedValue([]);
    const backendMessage = "Modelo timbrado 'peticao_inicial' ainda não publicado neste workspace.";
    const generateSpy = vi
      .spyOn(klmApi, 'generateLegalArtifact')
      .mockRejectedValue(new ApiError(409, backendMessage, backendMessage));

    renderWithClient(<LegalArtifactsTab demandId={DEMAND_ID} />);

    await screen.findByText('Nenhuma peça ainda. Gere a primeira a partir do modelo timbrado do escritório.');
    fireEvent.click(screen.getByRole('button', { name: /gerar/i }));

    const titleInput = await screen.findByPlaceholderText(/Petição inicial — Fulano x Beltrano/i);
    fireEvent.change(titleInput, { target: { value: 'Petição inicial — Novo caso' } });
    fireEvent.click(screen.getByRole('button', { name: /gerar peça/i }));

    await waitFor(() => {
      expect(generateSpy).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(toastSpy).toHaveBeenCalledWith(
        expect.objectContaining({ variant: 'destructive', description: backendMessage }),
      );
    });
    // O diálogo continua aberto — erro nunca é tratado como sucesso silencioso.
    expect(screen.getByText('Gerar peça a partir do modelo timbrado')).toBeInTheDocument();
  });

  it('citação não verificada aparece com o veredito honesto, nunca como "Verificada" sem fonte', async () => {
    vi.spyOn(klmApi, 'listLegalArtifactEditorVersions').mockResolvedValue([VERSION_FIXTURE]);
    vi.spyOn(klmApi, 'getLegalArtifactChecklist').mockResolvedValue({
      hasPlaceholder: false,
      missingVariables: [],
      hasProcuracao: true,
      hasCustasPagas: true,
      pendingItems: [],
      protocolabilityState: 'pendente',
    });
    vi.spyOn(klmApi, 'verifyLegalArtifactCitations').mockResolvedValue({
      artifactVersionId: VERSION_FIXTURE.id,
      allVerified: false,
      gateOpen: false,
      verifications: [
        {
          id: 'cit-1',
          workspaceId: 'ws-1',
          legalDemandId: DEMAND_ID,
          artifactVersionId: VERSION_FIXTURE.id,
          citationText: 'Súmula 297/STJ',
          citationKind: 'sumula',
          status: 'nao_verificada',
          sourceReference: null,
          trail: {},
          checkedByUserId: null,
          createdAt: '2026-07-20T12:00:00Z',
          updatedAt: '2026-07-20T12:00:00Z',
        },
      ],
    });

    renderWithClient(<LegalArtifactsTab demandId={DEMAND_ID} />);

    await screen.findByText('Petição inicial — Caso Teste');
    fireEvent.click(screen.getByRole('button', { name: /verificar citações/i }));

    expect(await screen.findByText('Súmula 297/STJ')).toBeInTheDocument();
    expect(await screen.findByText('Não verificada')).toBeInTheDocument();
    expect(screen.queryByText('Verificada')).not.toBeInTheDocument();
  });
});
