/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LocalStoragePanel } from '@/components/admin/LocalStoragePanel';

const storageApi = vi.hoisted(() => ({
  deleteObject: vi.fn(),
  download: vi.fn(),
  exportObject: vi.fn(),
  extendRetention: vi.fn(),
  list: vi.fn(),
  restore: vi.fn(),
  saveBlob: vi.fn(),
  upload: vi.fn(),
  versions: vi.fn(),
}));

vi.mock('@/services/local-storage', () => ({
  deleteLocalStorageObject: storageApi.deleteObject,
  downloadLocalStorageObject: storageApi.download,
  exportLocalStorageObject: storageApi.exportObject,
  extendLocalStorageRetention: storageApi.extendRetention,
  fetchLocalStorageObjects: storageApi.list,
  fetchLocalStorageVersions: storageApi.versions,
  restoreLocalStorageObject: storageApi.restore,
  saveBlob: storageApi.saveBlob,
  uploadLocalStorageObject: storageApi.upload,
}));

const available = {
  id: 'version-2', workspaceId: 'workspace-1', objectId: 'object-1', version: 2,
  originalFilename: 'negativa-plano.pdf', declaredContentType: 'application/pdf',
  detectedContentType: 'application/pdf', expectedByteSize: 1024, byteSize: 1024,
  checksumSha256: 'a'.repeat(64), state: 'available', avStatus: 'clean',
  retentionUntil: '2027-07-19T12:00:00Z', uploadedAt: '2026-07-19T12:00:00Z',
  createdAt: '2026-07-19T12:00:00Z', updatedAt: '2026-07-19T12:00:00Z',
};
const deleted = {
  ...available,
  id: 'deleted-1', objectId: 'object-2', version: 1, originalFilename: 'arquivo-antigo.pdf',
  state: 'deleted', deletedAt: '2026-07-19T13:00:00Z',
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocalStoragePanel />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LocalStoragePanel', () => {
  it('uploads, exposes versions and downloads only through local object actions', async () => {
    storageApi.list.mockResolvedValue([available, deleted]);
    storageApi.versions.mockResolvedValue([available, { ...available, id: 'version-1', version: 1 }]);
    storageApi.download.mockResolvedValue(new Blob(['pdf'], { type: 'application/pdf' }));
    storageApi.upload.mockResolvedValue(available);
    storageApi.restore.mockResolvedValue({ ...deleted, state: 'available' });

    renderPanel();

    expect(await screen.findByText('negativa-plano.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByText('negativa-plano.pdf'));
    expect(await screen.findByText('Versão 2')).toBeInTheDocument();
    expect(screen.getByText('Versão 1')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Baixar' }));
    await waitFor(() => expect(storageApi.download).toHaveBeenCalledWith('object-1', undefined));
    expect(storageApi.saveBlob).toHaveBeenCalledWith(expect.any(Blob), 'negativa-plano.pdf');

    const file = new File(['%PDF-1.7'], 'nova-negativa.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('Selecionar arquivo para upload'), {
      target: { files: [file] },
    });
    await waitFor(() => expect(storageApi.upload).toHaveBeenCalledWith(file, {
      objectId: undefined,
      retentionUntil: null,
    }));

    fireEvent.click(screen.getByRole('button', { name: 'Restaurar' }));
    await waitFor(() => expect(storageApi.restore).toHaveBeenCalledWith('object-2'));
  });
});
