/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { LegalProtocolTab } from '@/klm/components/legal/ProtocolFolderTab';
import { klmApi, type KlmLegalArtifactVersion, type KlmProtocolFolder } from '@/klm/api';

const permissionMock = vi.hoisted(() => vi.fn((_module: string, _action?: string) => true));
const toastMock = vi.hoisted(() => vi.fn());

vi.mock('@/store/app-context', () => ({
  useAppStore: () => ({
    can: permissionMock,
    state: { currentUser: { id: 'lawyer-1', name: 'Ana Advogada', email: 'ana@klmadvogados.com.br' } },
  }),
  useOptionalAppStore: () => ({ can: permissionMock, state: {} }),
}));

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }));

const demandId = 'demand-1';

const approvedArtifact: KlmLegalArtifactVersion = {
  id: 'artifact-1',
  workspaceId: 'workspace-1',
  matterId: 'matter-1',
  artifactKey: 'artifact-key-1',
  version: 2,
  kind: 'petition',
  title: 'Petição inicial',
  status: 'approved',
  content: { body: 'Conteúdo aprovado.' },
  sourceSnapshot: {},
  createdByUserId: 'user-1',
  approvalItemId: null,
  createdAt: '2026-07-20T10:00:00Z',
  updatedAt: '2026-07-24T10:00:00Z',
};

function baseManifest(overrides: Partial<KlmProtocolFolder['manifest']> = {}): KlmProtocolFolder['manifest'] {
  return {
    processo: { numeroCnj: '0700001-11.2026.8.07.0001', presente: true },
    tribunal: 'TJDFT — 1ª Vara Cível',
    tipoAto: 'petition',
    responsavel: { userId: 'user-1', nome: 'Ana Advogada' },
    versaoLiberada: { artifactVersionId: approvedArtifact.id, version: approvedArtifact.version, status: 'approved' },
    arquivos: [],
    tamanhoTotalBytes: 20480,
    assinaturaConfirmada: true,
    procuracao: { dispensada: false, presente: true },
    custas: { exigida: true, pagaNoFinanceiro: true },
    citacoes: { total: 2, naoVerificadas: [] },
    achadosCriticosAbertos: 0,
    placeholders: { presente: false },
    pendencias: [],
    geradoEm: '2026-07-26T12:00:00Z',
    estado: 'protocolavel',
    ...overrides,
  };
}

function makeFolder(overrides: Partial<KlmProtocolFolder> = {}): KlmProtocolFolder {
  return {
    id: 'protocol-folder-1',
    legalDemandId: demandId,
    matterId: 'matter-1',
    artifactVersionId: approvedArtifact.id,
    tribunal: 'TJDFT — 1ª Vara Cível',
    actType: 'petition',
    state: 'protocolavel',
    manifest: baseManifest(),
    files: [
      { order: 1, folder: '01_PETICAO', filename: 'peticao.docx', sourceType: 'artifact', sourceId: approvedArtifact.id, sha256: 'abc', sizeBytes: 20480, extension: 'docx' },
    ],
    signatureConfirmed: true,
    builtByUserId: 'user-1',
    builtAt: '2026-07-26T12:00:00Z',
    receiptRegisteredAt: null,
    receiptRegisteredByUserId: null,
    receiptReference: null,
    receiptNote: null,
    createdAt: '2026-07-26T12:00:00Z',
    updatedAt: '2026-07-26T12:00:00Z',
    ...overrides,
  };
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LegalProtocolTab demandId={demandId} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionMock.mockReset();
  permissionMock.mockReturnValue(true);
  toastMock.mockReset();
});

describe('LegalProtocolTab', () => {
  it('shows the explicit pending list when the assembled folder comes back "bloqueado" — never a false "ready"', async () => {
    vi.spyOn(klmApi, 'listDemandLegalArtifacts').mockResolvedValue([approvedArtifact]);
    const blocked = makeFolder({
      state: 'bloqueado',
      manifest: baseManifest({
        estado: 'bloqueado',
        placeholders: { presente: true },
        pendencias: [
          { item: 'placeholder', detail: 'O conteúdo do artefato tem placeholder/variável pendente.' },
          { item: 'citacao_nao_verificada', detail: 'Citação(ões) sem conferência na fonte: art. 5º, CF.' },
        ],
      }),
    });
    vi.spyOn(klmApi, 'buildProtocolFolder').mockResolvedValue(blocked);

    renderTab();

    fireEvent.click(await screen.findByRole('button', { name: /Montar pasta de protocolo/i }));

    expect(await screen.findByText(/2 pendência\(s\)/i)).toBeInTheDocument();
    expect(screen.getByText(/placeholder\/variável pendente/i)).toBeInTheDocument();
    expect(screen.getByText(/Citação\(ões\) sem conferência/i)).toBeInTheDocument();
    expect(screen.getByText('Bloqueado')).toBeInTheDocument();
  });

  it('never offers a "protocolar" action — only build/download/receipt, protocol itself stays human', async () => {
    vi.spyOn(klmApi, 'listDemandLegalArtifacts').mockResolvedValue([approvedArtifact]);
    vi.spyOn(klmApi, 'buildProtocolFolder').mockResolvedValue(makeFolder());

    renderTab();

    fireEvent.click(await screen.findByRole('button', { name: /Montar pasta de protocolo/i }));
    await screen.findByText('Protocolável');

    const buttons = screen.getAllByRole('button');
    const hasProtocolarAction = buttons.some((button) => /^protocolar\b/i.test(button.textContent ?? ''));
    expect(hasProtocolarAction).toBe(false);
    expect(screen.getByText(/A plataforma não protocola/i)).toBeInTheDocument();
  });

  it('registers the receipt as a posterior, human record — never triggers a protocol action', async () => {
    vi.spyOn(klmApi, 'listDemandLegalArtifacts').mockResolvedValue([approvedArtifact]);
    vi.spyOn(klmApi, 'buildProtocolFolder').mockResolvedValue(makeFolder());
    const registerReceipt = vi.spyOn(klmApi, 'registerProtocolReceipt').mockResolvedValue(
      makeFolder({
        receiptRegisteredAt: '2026-07-27T09:00:00Z',
        receiptRegisteredByUserId: 'user-1',
        receiptReference: '2026.0009876-1',
        receiptNote: null,
      }),
    );

    renderTab();

    fireEvent.click(await screen.findByRole('button', { name: /Montar pasta de protocolo/i }));
    await screen.findByText('Protocolável');

    fireEvent.change(screen.getByPlaceholderText(/2026\.0001234-5/), { target: { value: '2026.0009876-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Registrar recibo' }));

    await waitFor(() => expect(registerReceipt).toHaveBeenCalledWith(
      demandId,
      'protocol-folder-1',
      { reference: '2026.0009876-1', note: null },
    ));
    expect(await screen.findByText('Protocolado')).toBeInTheDocument();
    expect(screen.getByText('2026.0009876-1')).toBeInTheDocument();
  });
});
