import { vi } from 'vitest';
import { klmApi } from '@/klm/api';

export function mockEmptyCapabilityQueries() {
  vi.spyOn(klmApi, 'capabilities').mockResolvedValue({
    schemaVersion: '1.0',
    scope: 'klm-native-capabilities',
    capabilities: [],
    summary: {
      total: 0,
      available: 0,
      verified: 0,
      externalEffectVerified: 0,
      byDomain: { communications: 0, collaboration: 0, automation: 0, crm: 0 },
      byDisposition: {},
    },
    verification: {
      state: 'unverified',
      reason: 'Sem prova no cenário vazio.',
      checks: 0,
    },
  });
}
