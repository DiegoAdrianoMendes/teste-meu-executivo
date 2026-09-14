import type { ReactNode } from 'react';

import { DEV_TEST_FRONTEND_SCOPE } from '@klm/frontend-contract-fixtures';
import { isLocalSimulation } from '@/klm/runtime';
import { useAppStore } from '@/store/app-context';
import {
  frontendContractSource,
  type FrontendContractSource,
} from './frontend-contract-source';
import { UserPreferencesProvider } from './user-preferences-provider';
import { WorkspaceBrandProvider } from './workspace-brand-provider';

interface FrontendContractProvidersProps {
  source?: FrontendContractSource;
  children: ReactNode;
}

export function FrontendContractProviders({
  source = frontendContractSource,
  children,
}: FrontendContractProvidersProps) {
  const { state } = useAppStore();
  const devTestSimulation = isLocalSimulation
    && (source.environment === 'development' || source.environment === 'test');
  const workspaceId = state.activeWorkspace?.id
    ?? (devTestSimulation ? DEV_TEST_FRONTEND_SCOPE.workspaceId : null);
  const userId = state.currentUser?.id
    ?? (devTestSimulation ? DEV_TEST_FRONTEND_SCOPE.userId : null);

  return (
    <WorkspaceBrandProvider
      workspaceId={workspaceId}
      loadBrand={source.loadBrand}
    >
      <UserPreferencesProvider
        workspaceId={workspaceId}
        userId={userId}
        loadPreferences={source.loadUserPreferences}
        savePreferences={source.saveUserPreferences}
      >
        {children}
      </UserPreferencesProvider>
    </WorkspaceBrandProvider>
  );
}
