export type KlmRuntimeMode = 'local_simulation' | 'connected_local' | 'online';

const requestedMode = import.meta.env.VITE_KLM_RUNTIME_MODE as KlmRuntimeMode | undefined;

export function resolveKlmRuntimeMode(mode: KlmRuntimeMode | undefined, isDevelopment: boolean): KlmRuntimeMode {
  return mode ?? (isDevelopment ? 'local_simulation' : 'online');
}

export const klmRuntime = {
  mode: resolveKlmRuntimeMode(requestedMode, import.meta.env.DEV),
  externalActionsEnabled: import.meta.env.VITE_KLM_EXTERNAL_ACTIONS === 'true',
  awsEnabled: import.meta.env.VITE_KLM_AWS_ENABLED === 'true',
} as const;

export const isLocalSimulation = klmRuntime.mode === 'local_simulation';

export function assertExternalActionsDisabled() {
  if (klmRuntime.externalActionsEnabled || klmRuntime.awsEnabled) {
    throw new Error('O gate online deve permanecer fechado antes da aprovação do usuário.');
  }
}

assertExternalActionsDisabled();
