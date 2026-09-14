import { describe, expect, it } from 'vitest';

import { getAgentById } from '@/lib/agent-display';

describe('agent display identities', () => {
  it('uses the native KLM identity for known automated agents', () => {
    expect(getAgentById('klm_sdr')).toMatchObject({
      name: 'KLM SDR',
      role: 'Triagem comercial',
      type: 'ai',
    });
  });

  it('does not invent a personal identity for opaque workspace users', () => {
    expect(getAgentById('ag1')).toMatchObject({
      name: 'Usuário AG1',
      role: 'Usuário do workspace',
      type: 'human',
    });
  });

  it('keeps UUID identities short without resolving them across tenants', () => {
    expect(getAgentById('123e4567-e89b-12d3-a456-426614174000')?.name).toBe('Usuário 123e4567');
  });
});
