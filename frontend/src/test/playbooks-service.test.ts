import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import {
  approvePlaybook,
  clonePlaybook,
  getEffectivePlaybook,
  getPlaybookDiff,
  rollbackPlaybook,
  savePlaybookDraft,
  simulatePlaybook,
} from '@/services/playbooks';
import { playbookFixture } from '@/test/playbooks-fixture';

afterEach(() => vi.clearAllMocks());

describe('playbooks API contract', () => {
  it('preserves optimistic concurrency and governance reasons', async () => {
    http.apiFetch.mockResolvedValue(playbookFixture);
    await savePlaybookDraft('playbook/1', {
      name: playbookFixture.name,
      description: playbookFixture.description,
      scope: playbookFixture.scope,
      content: playbookFixture.content,
      reason: 'Revisão de honorários',
      expectedEtag: 'etag-2',
    });
    await approvePlaybook('playbook/1', 'Aprovado pelo administrador');
    await rollbackPlaybook('playbook/1', 1, 'Restaurar versão estável');

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/playbooks/playbook%2F1/draft', expect.objectContaining({ method: 'PUT', headers: { 'If-Match': '"etag-2"' } }));
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/playbooks/playbook%2F1/approve', { method: 'POST', body: JSON.stringify({ reason: 'Aprovado pelo administrador' }) });
    expect(http.apiFetch).toHaveBeenNthCalledWith(3, '/api/v1/playbooks/playbook%2F1/rollback/1', { method: 'POST', body: JSON.stringify({ reason: 'Restaurar versão estável' }) });
  });

  it('routes clone, diff, simulation and effective resolution without losing selectors', async () => {
    http.apiFetch.mockResolvedValue({});
    await clonePlaybook('p1', 'Cópia controlada');
    await getPlaybookDiff('p1', 2, 7);
    await simulatePlaybook('p1', { scenario: 'Urgência', customerMessage: 'Preciso de ajuda', context: { stage: 'triagem' }, version: 7 });
    await getEffectivePlaybook({ area: 'saude', channel: 'whatsapp', agent: 'sdr' });

    expect(http.apiFetch).toHaveBeenNthCalledWith(1, '/api/v1/playbooks/p1/clone', { method: 'POST', body: JSON.stringify({ name: 'Cópia controlada' }) });
    expect(http.apiFetch).toHaveBeenNthCalledWith(2, '/api/v1/playbooks/p1/diff?from_version=2&to_version=7');
    expect(http.apiFetch).toHaveBeenNthCalledWith(3, '/api/v1/playbooks/p1/simulations', expect.objectContaining({ method: 'POST' }));
    expect(http.apiFetch).toHaveBeenNthCalledWith(4, '/api/v1/playbooks/effective?area=saude&channel=whatsapp&agent=sdr');
  });
});
