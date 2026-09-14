/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LocalProvisioningPanel } from '@/components/admin/LocalProvisioningPanel';

const provisioningApi = vi.hoisted(() => ({
  execute: vi.fn(),
  plan: vi.fn(),
  state: vi.fn(),
}));

vi.mock('@/services/local-provisioning', () => ({
  executeLocalProvisioning: provisioningApi.execute,
  fetchLocalProvisioningPlan: provisioningApi.plan,
  fetchLocalProvisioningState: provisioningApi.state,
}));

const resourceDefinitions = [
  ['klm', 'storage'],
  ['communications', 'account'],
  ['collaboration', 'workspace'],
  ['automation', 'agent'],
  ['crm', 'pipeline'],
  ['operations', 'component'],
] as const;
const resources = resourceDefinitions.map(([domain, resourceType], index) => ({
  key: `${domain}:resource:${index}`,
  provider: domain,
  resourceType,
  logicalKey: `resource-${index}`,
  action: 'create' as const,
  status: 'pending' as const,
  desiredHash: `hash-${index}`,
  observedHash: null,
  adapter: 'fake_local' as const,
  externalEffects: 0,
}));
const plan = {
  workspaceId: 'workspace-1',
  digest: 'abcdef1234567890abcdef1234567890',
  schemaVersion: '1.0',
  generatedAt: '2026-07-19T10:00:00Z',
  resources,
  creates: resources.length,
  updates: 0,
  noops: 0,
  ready: false,
  externalEffects: 0,
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocalProvisioningPanel />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LocalProvisioningPanel', () => {
  it('shows every native domain and applies the declarative plan', async () => {
    provisioningApi.plan.mockResolvedValue(plan);
    provisioningApi.state.mockResolvedValue({
      workspaceId: 'workspace-1', configured: false, desiredDigest: plan.digest,
      resources, ready: false, externalEffects: 0,
    });
    provisioningApi.execute.mockResolvedValue({
      action: 'apply', replayed: false, intentId: 'intent-1', plan,
      state: { workspaceId: 'workspace-1', configured: true, documentVersion: 1, desiredDigest: plan.digest, resources: [], ready: true, externalEffects: 0 },
      externalEffects: 0,
    });

    renderPanel();

    for (const label of ['Núcleo Meu Executivo', 'Comunicações', 'Colaboração', 'Automação', 'CRM jurídico', 'Operações locais']) {
      expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
    }
    expect(screen.getByText('0 efeitos externos')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Aplicar plano' }));
    await waitFor(() => expect(provisioningApi.execute).toHaveBeenCalledWith('apply'));
  });
});
