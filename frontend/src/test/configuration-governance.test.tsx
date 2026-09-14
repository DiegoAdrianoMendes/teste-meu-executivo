/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ConfigurationGovernancePanel } from '@/components/admin/ConfigurationGovernancePanel';

const configurationApi = vi.hoisted(() => ({
  fetchDocument: vi.fn(),
  fetchEffective: vi.fn(),
  fetchVersions: vi.fn(),
  rollback: vi.fn(),
  save: vi.fn(),
}));

vi.mock('@/services/configurations', () => ({
  fetchConfigurationDocument: configurationApi.fetchDocument,
  fetchConfigurationVersions: configurationApi.fetchVersions,
  fetchEffectiveConfiguration: configurationApi.fetchEffective,
  rollbackConfigurationDocument: configurationApi.rollback,
  saveConfigurationDocument: configurationApi.save,
}));

const document = {
  id: 'document-1',
  namespace: 'brand.identity',
  scope: 'workspace',
  scopeKey: 'workspace-1:brand.identity',
  schemaVersion: '1.0',
  currentVersion: 2,
  etag: 'etag-2',
  payload: { name: 'KLM atual' },
  createdAt: '2026-07-19T12:00:00Z',
  updatedAt: '2026-07-19T12:30:00Z',
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfigurationGovernancePanel
        roles={[{ id: 'admin', name: 'Administrador', description: '', permissions: [], color: '#000' }]}
        users={[{
          id: 'user-1',
          name: 'Admin KLM',
          initials: 'AK',
          email: 'admin@klm.local',
          type: 'human',
          role: 'admin',
          color: '#000',
          isActive: true,
          lastLogin: '-',
          roles: ['admin'],
          directPermissions: [],
        }]}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ConfigurationGovernancePanel', () => {
  it('edits with ETag, records a reason and restores an older version', async () => {
    configurationApi.fetchDocument.mockResolvedValue(document);
    configurationApi.fetchEffective.mockResolvedValue({
      namespace: 'brand.identity',
      effective: { name: 'KLM efetivo', colors: { primary: '#DCA74C' } },
      etag: 'effective-etag',
      layers: [{ scope: 'workspace', scopeKey: document.scopeKey, version: 2, etag: 'etag-2' }],
    });
    configurationApi.fetchVersions.mockResolvedValue([
      {
        id: 'version-2', version: 2, schemaVersion: '1.0', payload: document.payload,
        payloadHash: 'abcdef1234567890', reason: 'Versão atual', createdAt: document.updatedAt,
      },
      {
        id: 'version-1', version: 1, schemaVersion: '1.0', payload: { name: 'KLM anterior' },
        payloadHash: '123456abcdef7890', reason: 'Versão inicial', createdAt: document.createdAt,
      },
    ]);
    configurationApi.save.mockResolvedValue({ ...document, currentVersion: 3, etag: 'etag-3' });
    configurationApi.rollback.mockResolvedValue({ ...document, currentVersion: 3, etag: 'etag-3' });

    renderPanel();

    expect(await screen.findByText('v2')).toBeInTheDocument();
    expect(screen.getByText(/KLM efetivo/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('JSON da camada'), {
      target: { value: '{"name":"KLM revisado"}' },
    });
    fireEvent.change(screen.getByPlaceholderText(/ajuste aprovado/), {
      target: { value: 'Ajuste aprovado pelo administrador' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Publicar versão' }));

    await waitFor(() => expect(configurationApi.save).toHaveBeenCalledWith(
      { namespace: 'brand.identity', scope: 'workspace', roleKey: null, userId: null },
      { name: 'KLM revisado' },
      {
        etag: 'etag-2',
        reason: 'Ajuste aprovado pelo administrador',
        schemaVersion: '1.0',
      },
    ));

    const restoreButtons = screen.getAllByRole('button', { name: 'Restaurar' });
    fireEvent.click(restoreButtons[1]);
    await waitFor(() => expect(configurationApi.rollback).toHaveBeenCalledWith(
      { namespace: 'brand.identity', scope: 'workspace', roleKey: null, userId: null },
      1,
      'etag-2',
    ));
  });
});
