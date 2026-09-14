/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { LegalDocumentsTab } from '@/klm/components/legal/CaseDocumentsTab';
import {
  klmApi,
  type KlmLegalDemand,
  type KlmLegalDocumentAsset,
  type KlmLegalDocumentIngestResult,
  type KlmLegalDocumentShareDownload,
  type KlmLegalDocumentShareLinkIssued,
} from '@/klm/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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

const DEMAND_FIXTURE: KlmLegalDemand = {
  id: 'demand-1',
  workspaceId: 'ws-1',
  externalRef: null,
  nature: 'judicial',
  area: 'Cível',
  subarea: null,
  clientId: 'client-1',
  parties: [],
  unit: null,
  team: null,
  responsibleUserId: null,
  strategy: null,
  risk: 'medio',
  value: null,
  phase: 'em_andamento',
  sla: {},
  playbookRef: null,
  documentsRequired: [],
  matterId: 'matter-1',
  contractRef: null,
  financeRef: null,
  createdAt: '2026-07-01T12:00:00Z',
  updatedAt: '2026-07-01T12:00:00Z',
};

const DOCUMENT_FIXTURE: KlmLegalDocumentAsset = {
  id: 'doc-1',
  storageObjectId: 'storage-1',
  contentSha256: 'a'.repeat(64),
  originalFilename: 'peticao-inicial.pdf',
  matterId: 'matter-1',
  clientId: 'client-1',
  documentType: 'peticao_inicial',
  classificationMethod: 'indisponivel',
  classificationConfidence: null,
  ocrStatus: 'ocr_pendente',
  createdAt: '2026-07-20T10:00:00Z',
  storage: {
    state: 'available',
    avStatus: 'clean',
    quarantineReason: null,
    byteSize: 204800,
    version: 1,
  },
};

const QUARANTINED_DOCUMENT_FIXTURE: KlmLegalDocumentAsset = {
  ...DOCUMENT_FIXTURE,
  id: 'doc-quarantined-1',
  originalFilename: 'anexo-suspeito.exe',
  storage: {
    state: 'quarantined',
    avStatus: 'infected',
    quarantineReason: 'Assinatura de malware detectada',
    byteSize: 5120,
    version: 1,
  },
};

const OTHER_CASE_QUARANTINED_FIXTURE: KlmLegalDocumentAsset = {
  ...QUARANTINED_DOCUMENT_FIXTURE,
  id: 'doc-quarantined-other-case',
  matterId: 'matter-other',
};

function mockDemand(overrides: Partial<KlmLegalDemand> = {}) {
  vi.spyOn(klmApi, 'getLegalDemand').mockResolvedValue({ ...DEMAND_FIXTURE, ...overrides });
}

describe('LegalDocumentsTab (DMS jurídico — Sprint A aba Documentos)', () => {
  it('mostra estado honesto quando a demanda ainda não tem caso (matterId) vinculado', async () => {
    mockDemand({ matterId: null });
    const listSpy = vi.spyOn(klmApi, 'listLegalDocuments');

    renderWithClient(<LegalDocumentsTab demandId="demand-1" />);

    expect(await screen.findByText('Documentos ainda não disponíveis para esta demanda')).toBeInTheDocument();
    expect(listSpy).not.toHaveBeenCalled();
  });

  it('lista os documentos do caso com tipo e status de OCR honestos', async () => {
    mockDemand();
    vi.spyOn(klmApi, 'listLegalDocuments').mockResolvedValue([DOCUMENT_FIXTURE]);
    vi.spyOn(klmApi, 'listQuarantinedLegalDocuments').mockResolvedValue([]);

    renderWithClient(<LegalDocumentsTab demandId="demand-1" />);

    expect(await screen.findByText('peticao-inicial.pdf')).toBeInTheDocument();
    expect(screen.getByText('Petição inicial')).toBeInTheDocument();
    expect(screen.getByText('OCR pendente')).toBeInTheDocument();
    expect(screen.getByText('Disponível')).toBeInTheDocument();
  });

  it('envia um arquivo pelo input de upload e atualiza a listagem', async () => {
    mockDemand();
    vi.spyOn(klmApi, 'listLegalDocuments')
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([DOCUMENT_FIXTURE]);
    vi.spyOn(klmApi, 'listQuarantinedLegalDocuments').mockResolvedValue([]);
    const ingestResult: KlmLegalDocumentIngestResult = {
      asset: {
        id: DOCUMENT_FIXTURE.id,
        storageObjectId: DOCUMENT_FIXTURE.storageObjectId,
        contentSha256: DOCUMENT_FIXTURE.contentSha256,
        originalFilename: DOCUMENT_FIXTURE.originalFilename,
        matterId: DOCUMENT_FIXTURE.matterId,
        clientId: DOCUMENT_FIXTURE.clientId,
        documentType: DOCUMENT_FIXTURE.documentType,
        classificationMethod: DOCUMENT_FIXTURE.classificationMethod,
        classificationConfidence: null,
        ocrStatus: DOCUMENT_FIXTURE.ocrStatus,
        createdAt: DOCUMENT_FIXTURE.createdAt,
      },
      reference: {
        id: 'ref-1',
        documentAssetId: DOCUMENT_FIXTURE.id,
        source: 'upload_manual',
        originalFilename: DOCUMENT_FIXTURE.originalFilename,
        externalRef: null,
        matterId: 'matter-1',
        createdAt: DOCUMENT_FIXTURE.createdAt,
      },
      createdNewAsset: true,
      deduplicated: false,
    };
    const uploadSpy = vi.spyOn(klmApi, 'uploadLegalDocument').mockResolvedValue(ingestResult);

    renderWithClient(<LegalDocumentsTab demandId="demand-1" />);

    await screen.findByText('Nenhum documento enviado para este caso ainda.');

    const file = new File(['conteudo'], 'peticao-inicial.pdf', { type: 'application/pdf' });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => {
      expect(uploadSpy).toHaveBeenCalledTimes(1);
    });
    expect(uploadSpy).toHaveBeenCalledWith({ file, matterId: 'matter-1', clientId: 'client-1' });
    expect(await screen.findByText('peticao-inicial.pdf')).toBeInTheDocument();
  });

  it('compartilha um documento com expiração obrigatória e mostra a trilha de download', async () => {
    mockDemand();
    vi.spyOn(klmApi, 'listLegalDocuments').mockResolvedValue([DOCUMENT_FIXTURE]);
    vi.spyOn(klmApi, 'listQuarantinedLegalDocuments').mockResolvedValue([]);
    const future = new Date(Date.now() + 24 * 60 * 60 * 1000);
    future.setMinutes(future.getMinutes() - future.getTimezoneOffset());
    const futureLocal = future.toISOString().slice(0, 16);
    const issued: KlmLegalDocumentShareLinkIssued = {
      id: 'share-1',
      documentAssetId: DOCUMENT_FIXTURE.id,
      token: 'raw-token-abc123',
      expiresAt: new Date(futureLocal).toISOString(),
      watermarkEnabled: true,
      maxDownloads: null,
    };
    const createSpy = vi.spyOn(klmApi, 'createLegalDocumentShareLink').mockResolvedValue(issued);
    const downloads: KlmLegalDocumentShareDownload[] = [
      { id: 'dl-1', shareLinkId: 'share-1', downloadedAt: '2026-07-25T09:00:00Z', downloadedByUserId: 'user-1', downloaderIp: '10.0.0.1' },
    ];
    vi.spyOn(klmApi, 'listLegalDocumentShareDownloads').mockResolvedValue(downloads);

    renderWithClient(<LegalDocumentsTab demandId="demand-1" />);

    const shareButton = await screen.findByRole('button', { name: /compartilhar/i });
    fireEvent.click(shareButton);

    const expiresInput = await screen.findByLabelText('Expira em');
    fireEvent.change(expiresInput, { target: { value: futureLocal } });

    const submitButton = screen.getByRole('button', { name: /gerar link/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledTimes(1);
    });
    expect(createSpy).toHaveBeenCalledWith(DOCUMENT_FIXTURE.id, expect.objectContaining({ watermarkEnabled: true, maxDownloads: null }));

    expect(await screen.findByText('raw-token-abc123')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/10\.0\.0\.1/)).toBeInTheDocument();
    });
  });

  it('não permite compartilhar um documento em quarentena e mostra o estado honesto da fila', async () => {
    mockDemand();
    vi.spyOn(klmApi, 'listLegalDocuments').mockResolvedValue([QUARANTINED_DOCUMENT_FIXTURE]);
    vi.spyOn(klmApi, 'listQuarantinedLegalDocuments').mockResolvedValue([
      QUARANTINED_DOCUMENT_FIXTURE,
      OTHER_CASE_QUARANTINED_FIXTURE,
    ]);

    renderWithClient(<LegalDocumentsTab demandId="demand-1" />);

    expect((await screen.findAllByText('anexo-suspeito.exe')).length).toBeGreaterThan(0);
    expect(screen.getByText(/Assinatura de malware detectada/)).toBeInTheDocument();

    // A quarentena de outro caso (matter-other) nunca aparece nesta aba.
    expect(screen.queryAllByText('anexo-suspeito.exe')).toHaveLength(2); // uma na lista, outra na quarentena deste caso
    expect(screen.getAllByText('Em quarentena').length).toBeGreaterThan(0);

    const shareButton = screen.getByRole('button', { name: /compartilhar/i });
    expect(shareButton).toBeDisabled();
  });
});
