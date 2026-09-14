/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import type { UserPreferences } from '@/types/frontend-contracts';
import { buildDefaultUserPreferences } from '@/branding/workspace-defaults';
import { applyUserPreferencesToDocument, resolveUserTheme } from './frontend-contract-dom';
import {
  FrontendContractConfigurationError,
  userPreferencesScopeKey,
  type UserPreferencesScope,
} from './frontend-contract-source';
import type { FrontendContractLoadStatus } from './workspace-brand-provider';

export type UserPreferencesPatch = Partial<Omit<
  UserPreferences,
  'id' | 'workspaceId' | 'userId' | 'version' | 'updatedAt'
>>;

export interface UserPreferencesContextValue {
  scope: UserPreferencesScope | null;
  preferences: UserPreferences | null;
  resolvedTheme: 'light' | 'dark' | null;
  status: FrontendContractLoadStatus;
  error: Error | null;
  persistenceError: Error | null;
  updatePreferences: (patch: UserPreferencesPatch) => Promise<void>;
}

interface UserPreferencesProviderProps {
  workspaceId: string | null;
  userId: string | null;
  preferences?: UserPreferences | null;
  loadPreferences?: (scope: UserPreferencesScope) => Promise<UserPreferences>;
  savePreferences?: (scope: UserPreferencesScope, value: UserPreferences) => Promise<void>;
  loadingFallback?: ReactNode;
  errorFallback?: ReactNode | ((error: Error) => ReactNode);
  children: ReactNode;
}

interface PreferencesState {
  scopeKey: string | null;
  status: FrontendContractLoadStatus;
  preferences: UserPreferences | null;
  error: Error | null;
}

const UserPreferencesContext = createContext<UserPreferencesContextValue | undefined>(undefined);
const PREFERENCES_LOAD_TIMEOUT_MS = 8_000;
const PREFERENCES_LOAD_ATTEMPTS = 2;

function boundedPreferencesLoad(load: () => Promise<UserPreferences>): Promise<UserPreferences> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      reject(new Error('As preferências não responderam dentro do tempo esperado.'));
    }, PREFERENCES_LOAD_TIMEOUT_MS);
    void load().then(
      (preferences) => {
        window.clearTimeout(timeout);
        resolve(preferences);
      },
      (error) => {
        window.clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

async function loadPreferencesWithFiniteRetry(
  load: () => Promise<UserPreferences>,
): Promise<UserPreferences> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= PREFERENCES_LOAD_ATTEMPTS; attempt += 1) {
    try {
      return await boundedPreferencesLoad(load);
    } catch (error) {
      lastError = error;
      if (attempt < PREFERENCES_LOAD_ATTEMPTS) {
        await new Promise((resolve) => window.setTimeout(resolve, 150));
      }
    }
  }
  throw lastError ?? new Error('Falha desconhecida ao carregar as preferências.');
}

function getSystemPrefersDark(): boolean {
  try {
    return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    return false;
  }
}

function configurationError(scope: UserPreferencesScope): FrontendContractConfigurationError {
  return new FrontendContractConfigurationError({
    code: 'missing_contract',
    contractName: 'UserPreferences',
    scopeKey: userPreferencesScopeKey(scope),
    message: `Nenhuma fonte de UserPreferences foi configurada para o escopo ${userPreferencesScopeKey(scope)}.`,
  });
}

function assertScope(preferences: UserPreferences, scope: UserPreferencesScope): UserPreferences {
  if (preferences.workspaceId !== scope.workspaceId || preferences.userId !== scope.userId) {
    throw new FrontendContractConfigurationError({
      code: 'scope_mismatch',
      contractName: 'UserPreferences',
      scopeKey: userPreferencesScopeKey(scope),
      message: 'UserPreferences não pertence ao par usuário/workspace ativo.',
    });
  }
  return preferences;
}

function defaultLoadingFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground" role="status">
      Carregando preferências do usuário...
    </div>
  );
}

function defaultErrorFallback(error: Error) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6 text-center" role="alert">
      <div className="max-w-xl border border-destructive/30 bg-card p-5 text-sm text-destructive">
        Preferências do usuário indisponíveis. {error.message}
      </div>
    </div>
  );
}

export function UserPreferencesProvider({
  workspaceId,
  userId,
  preferences: providedPreferences,
  loadPreferences,
  savePreferences,
  loadingFallback,
  errorFallback,
  children,
}: UserPreferencesProviderProps) {
  const scope = useMemo(
    () => workspaceId && userId ? { workspaceId, userId } : null,
    [userId, workspaceId],
  );
  const scopeKey = scope ? userPreferencesScopeKey(scope) : null;
  const [state, setState] = useState<PreferencesState>({
    scopeKey,
    status: scope ? 'loading' : 'idle',
    preferences: null,
    error: null,
  });
  const [persistenceError, setPersistenceError] = useState<Error | null>(null);
  const [systemPrefersDark, setSystemPrefersDark] = useState(getSystemPrefersDark);
  const preferencesRef = useRef<UserPreferences | null>(null);
  const activeScopeKeyRef = useRef(scopeKey);
  activeScopeKeyRef.current = scopeKey;

  useEffect(() => {
    let cancelled = false;
    if (!scope || !scopeKey) {
      preferencesRef.current = null;
      setState({ scopeKey: null, status: 'idle', preferences: null, error: null });
      setPersistenceError(null);
      return () => {
        cancelled = true;
      };
    }

    setState({ scopeKey, status: 'loading', preferences: null, error: null });
    setPersistenceError(null);
    preferencesRef.current = null;
    const resolvePreferences = async () => {
      try {
        const resolved = providedPreferences ?? await (loadPreferences
          ? loadPreferencesWithFiniteRetry(() => loadPreferences(scope))
          : Promise.reject(configurationError(scope)));
        const scopedPreferences = assertScope(resolved, scope);
        if (!cancelled) {
          preferencesRef.current = scopedPreferences;
          setState({ scopeKey, status: 'ready', preferences: scopedPreferences, error: null });
        }
      } catch (error) {
        if (!cancelled) {
          const normalizedError = error instanceof Error
            ? error
            : new Error(
              typeof error === 'object' && error !== null && 'message' in error
                ? String(error.message)
                : 'Preferências temporariamente indisponíveis.',
            );
          // Preferences only affect presentation and never grant permissions.
          // Discard any invalid/cross-scope payload and continue with freshly
          // scoped defaults instead of blanking every application route. The
          // normalized error remains available for diagnostics and a later
          // navigation can retry the canonical API.
          const fallback = buildDefaultUserPreferences(scope);
          preferencesRef.current = fallback;
          setState({ scopeKey, status: 'ready', preferences: fallback, error: normalizedError });
        }
      }
    };
    void resolvePreferences();

    return () => {
      cancelled = true;
    };
  }, [loadPreferences, providedPreferences, scope, scopeKey]);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => setSystemPrefersDark(event.matches);
    setSystemPrefersDark(media.matches);
    media.addEventListener?.('change', onChange);
    return () => media.removeEventListener?.('change', onChange);
  }, []);

  const stateForScope = state.scopeKey === scopeKey
    ? state
    : { scopeKey, status: scope ? 'loading' as const : 'idle' as const, preferences: null, error: null };

  useEffect(() => {
    if (stateForScope.status !== 'ready' || !stateForScope.preferences) return undefined;
    return applyUserPreferencesToDocument(stateForScope.preferences, systemPrefersDark);
  }, [stateForScope.preferences, stateForScope.status, systemPrefersDark]);

  const updatePreferences = useCallback(async (patch: UserPreferencesPatch) => {
    if (!scope || !scopeKey || !preferencesRef.current) {
      throw configurationError(scope ?? { workspaceId: workspaceId ?? 'none', userId: userId ?? 'none' });
    }
    const previous = preferencesRef.current;
    const next: UserPreferences = {
      ...previous,
      ...patch,
      id: previous.id,
      workspaceId: scope.workspaceId,
      userId: scope.userId,
      version: previous.version + 1,
      updatedAt: new Date().toISOString(),
    };
    preferencesRef.current = next;
    setPersistenceError(null);
    setState({ scopeKey, status: 'ready', preferences: next, error: null });

    try {
      if (!savePreferences) {
        throw new FrontendContractConfigurationError({
          code: 'persistence_unavailable',
          contractName: 'UserPreferences',
          scopeKey,
          message: 'Nenhum persistidor de UserPreferences foi configurado.',
        });
      }
      await savePreferences(scope, next);
    } catch (error) {
      const normalizedError = error instanceof Error ? error : new Error(String(error));
      if (activeScopeKeyRef.current === scopeKey && preferencesRef.current === next) {
        preferencesRef.current = previous;
        setState({ scopeKey, status: 'ready', preferences: previous, error: null });
      }
      if (activeScopeKeyRef.current === scopeKey) setPersistenceError(normalizedError);
      throw normalizedError;
    }
  }, [savePreferences, scope, scopeKey, userId, workspaceId]);

  const resolvedTheme = stateForScope.preferences
    ? resolveUserTheme(stateForScope.preferences.theme, systemPrefersDark)
    : null;
  const value = useMemo<UserPreferencesContextValue>(() => ({
    scope,
    preferences: stateForScope.preferences,
    resolvedTheme,
    status: stateForScope.status,
    error: stateForScope.error,
    persistenceError,
    updatePreferences,
  }), [
    persistenceError,
    resolvedTheme,
    scope,
    stateForScope.error,
    stateForScope.preferences,
    stateForScope.status,
    updatePreferences,
  ]);

  let content = children;
  if (stateForScope.status === 'loading') {
    content = loadingFallback ?? defaultLoadingFallback();
  } else if (stateForScope.status === 'error' && stateForScope.error) {
    content = typeof errorFallback === 'function'
      ? errorFallback(stateForScope.error)
      : errorFallback ?? defaultErrorFallback(stateForScope.error);
  }

  return (
    <UserPreferencesContext.Provider value={value}>
      {content}
    </UserPreferencesContext.Provider>
  );
}

export function useUserPreferences(): UserPreferencesContextValue {
  const context = useContext(UserPreferencesContext);
  if (!context) throw new Error('useUserPreferences deve ser usado dentro de UserPreferencesProvider.');
  return context;
}

export function useOptionalUserPreferences(): UserPreferencesContextValue | null {
  return useContext(UserPreferencesContext) ?? null;
}
