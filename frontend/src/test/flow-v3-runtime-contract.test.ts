import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('@/services/http', () => ({
  API_BASE: '',
  apiBlob: vi.fn(),
  apiDownload: vi.fn(),
  apiFetch: http.apiFetch,
  apiUpload: vi.fn(),
}));

import { klmApi } from '@/klm/api';

const graph = {
  nodes: [
    { id: 'trigger-1', type: 'trigger', data: { label: 'Entrada' } },
    { id: 'agent-1', type: 'agent', data: { label: 'Agente', agent: 'hermes' } },
  ],
  edges: [{ id: 'edge-1', source: 'trigger-1', target: 'agent-1' }],
};

const approved = {
  valid: true,
  node_count: 2,
  edge_count: 1,
  reachable_node_count: 2,
  estimated_cost_units: 11,
  external_action_count: 0,
  human_gate_count: 0,
  issues: [],
};

afterEach(() => vi.clearAllMocks());

describe('Flow V3 runtime transport', () => {
  it('calls validate before simulate with the exact same graph contract', async () => {
    http.apiFetch.mockResolvedValueOnce(approved).mockResolvedValueOnce(approved);

    await klmApi.validateFlowGraphDraft(graph);
    await klmApi.simulateFlowGraphDraft(graph);

    expect(http.apiFetch.mock.calls).toEqual([
      ['/api/v1/flows/validate', { method: 'POST', body: JSON.stringify(graph) }],
      ['/api/v1/flows/simulate', { method: 'POST', body: JSON.stringify(graph) }],
    ]);
  });

  it('preserves a runtime-invalid result instead of converting it into success', async () => {
    const blocked = {
      ...approved,
      valid: false,
      issues: [{
        code: 'unbounded_cycle',
        severity: 'error',
        message: 'Ciclo sem nó de repetição limitado por max_iterations.',
        node_ids: ['trigger-1', 'agent-1'],
      }],
    };
    http.apiFetch.mockResolvedValueOnce(blocked);

    await expect(klmApi.validateFlowGraphDraft(graph)).resolves.toEqual(blocked);
    expect(http.apiFetch).toHaveBeenCalledOnce();
  });

  it('propagates a transport failure so the editor can fail closed', async () => {
    http.apiFetch.mockRejectedValueOnce(new Error('runtime indisponível'));

    await expect(klmApi.validateFlowGraphDraft(graph)).rejects.toThrow('runtime indisponível');
    expect(http.apiFetch).toHaveBeenCalledOnce();
  });
});
