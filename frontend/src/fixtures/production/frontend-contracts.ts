import type { DevTestFrontendContractFixtures } from '@/fixtures/dev-test/frontend-contracts';

export const DEV_TEST_FRONTEND_SCOPE = {
  workspaceId: 'fixtures-disabled',
  userId: 'fixtures-disabled',
} as const;

export function loadDevTestFrontendContractFixtures(
  _scope: { workspaceId: string; userId: string },
): DevTestFrontendContractFixtures {
  throw new Error('Fixtures frontend-first não estão disponíveis no build de produção.');
}
