import { afterEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/services/http', () => ({ apiFetch: http.apiFetch }));

import { klmApi } from '@/klm/api';

afterEach(() => vi.clearAllMocks());

describe('KLM native experience API contract', () => {
  it('consumes the canonical capability registry without provider adaptation', async () => {
    const canonicalRegistry = {
      schemaVersion: '2.0',
      scope: 'test',
      capabilities: [{
        id: 'communications.conversation-composer',
        domain: 'communications',
        key: 'conversation-composer',
        nativeRoute: '/app/capacidades/communications/conversation-composer?mode=review',
      }],
      summary: {
        total: 1,
        byDomain: { communications: 1, collaboration: 0, automation: 0, crm: 0 },
      },
      qaVerification: {},
    };
    http.apiFetch.mockResolvedValue(canonicalRegistry);

    const registry = await klmApi.capabilities();

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/capabilities');
    expect(registry).toBe(canonicalRegistry);
    expect(registry.capabilities[0]).toMatchObject({
      id: 'communications.conversation-composer',
      domain: 'communications',
      nativeRoute: '/app/capacidades/communications/conversation-composer?mode=review',
    });
    expect(registry.capabilities[0]).not.toHaveProperty('provider');
    expect(registry.capabilities[0]).not.toHaveProperty('provenance');
    expect(registry.summary.byDomain).toEqual({
      communications: 1,
      collaboration: 0,
      automation: 0,
      crm: 0,
    });
  });

  it('keeps inventory navigation on the public domain contract', async () => {
    http.apiFetch.mockResolvedValue({
      items: [{
        id: 'fu_0123456789abcdef01234567',
        domain: 'communications',
        name: 'Gestão de contatos',
        category: 'api',
        capability: 'contacts',
        maturity: 'native_primary',
        disposition: 'native_primary',
        summary: 'Função disponível na operação KLM.',
      }],
    });

    const result = await klmApi.functionUnits({ capability: 'communications.contacts' });

    expect(http.apiFetch).toHaveBeenCalledWith(
      '/api/v1/capabilities/functions?capability=communications.contacts',
    );
    expect(result.items[0]).toMatchObject({
      id: 'fu_0123456789abcdef01234567',
      domain: 'communications',
      capability: 'contacts',
    });
  });

  it('keeps the KLM domain while creating a controlled external intent', async () => {
    http.apiFetch.mockResolvedValue({ id: 'intent-1' });

    await klmApi.createDomainExternalIntent({
      domain: 'automation',
      actionType: 'agent_quality_version_publish',
      idempotencyKey: 'quality:1',
      payload: { externalActionsExecuted: 0 },
    });

    expect(http.apiFetch).toHaveBeenCalledWith('/api/v1/klm/external-intents', {
      method: 'POST',
      body: JSON.stringify({
        actionType: 'agent_quality_version_publish',
        idempotencyKey: 'quality:1',
        payload: { externalActionsExecuted: 0 },
        provider: 'automation',
      }),
    });
  });

  it('keeps contextual product surfaces on native routes', async () => {
    http.apiFetch.mockResolvedValue({});

    await klmApi.nativeInboxThreadContext('thread-1');
    await klmApi.reconcileNativeInboxThread('thread-1');
    await klmApi.updateNativeInboxRouting('thread-1', { status: 'pending' });
    await klmApi.nativeCollaborationThreadContext('thread-1');
    await klmApi.reconcileNativeCollaborationThread('thread-1');
    await klmApi.nativeAgentContext('30d');
    await klmApi.reconcileNativeAgents();
    await klmApi.nativeFlowContext('flow-1');
    await klmApi.reconcileNativeFlow('flow-1');
    await klmApi.nativeCRMFunnelContext();
    await klmApi.nativeCRMMatterContext('matter-1');
    await klmApi.nativeCRMWorkContext();
    await klmApi.nativeCRMFinanceContext();

    const paths = http.apiFetch.mock.calls.map(([path]) => String(path));
    expect(paths).toEqual([
      '/api/v1/communications/threads/thread-1',
      '/api/v1/communications/threads/thread-1/reconcile',
      '/api/v1/communications/threads/thread-1/routing',
      '/api/v1/collaboration/threads/thread-1',
      '/api/v1/collaboration/threads/thread-1/reconcile',
      '/api/v1/automation/agents?time_range=30d',
      '/api/v1/automation/agents/reconcile',
      '/api/v1/automation/flows/flow-1',
      '/api/v1/automation/flows/flow-1/reconcile',
      '/api/v1/crm/funnel',
      '/api/v1/crm/matters/matter-1',
      '/api/v1/crm/work',
      '/api/v1/crm/finance',
    ]);
    expect(paths.some((path) => path.includes('/oss-'))).toBe(false);
  });
});
