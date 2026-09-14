import type { KlmWorkspaceMode } from './navigation';

export type WorkspaceNavigationVariant = 'preserved' | 'legal_operations';
export type WorkspaceTopbarVariant = 'preserved' | 'legal_operations';
export type WorkspaceTaskExperience = 'standard' | 'legal_360';

export interface WorkspaceExperienceConfig {
  mode: KlmWorkspaceMode;
  navigationVariant: WorkspaceNavigationVariant;
  topbarVariant: WorkspaceTopbarVariant;
  taskExperience: WorkspaceTaskExperience;
}

/**
 * Contrato de isolamento visual. Geral e Comercial mantêm o shell histórico;
 * somente Jurídico opta pela composição operacional ampliada.
 */
export function workspaceExperienceForMode(mode: KlmWorkspaceMode): WorkspaceExperienceConfig {
  if (mode === 'legal') {
    return {
      mode,
      navigationVariant: 'legal_operations',
      topbarVariant: 'legal_operations',
      taskExperience: 'legal_360',
    };
  }
  return {
    mode,
    navigationVariant: 'preserved',
    topbarVariant: 'preserved',
    taskExperience: 'standard',
  };
}

export interface KlmOutletContext {
  workspaceExperience: WorkspaceExperienceConfig;
}
