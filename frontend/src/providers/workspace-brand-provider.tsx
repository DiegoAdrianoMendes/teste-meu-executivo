/* eslint-disable react-refresh/only-export-components */

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import type { Brand } from '@/types/frontend-contracts';
import { buildDefaultBrand } from '@/branding/workspace-defaults';
import { applyWorkspaceBrandToDocument } from './frontend-contract-dom';
import {
  FrontendContractConfigurationError,
  type WorkspaceBrandScope,
} from './frontend-contract-source';

export type FrontendContractLoadStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface WorkspaceBrandContextValue {
  scope: WorkspaceBrandScope | null;
  brand: Brand | null;
  status: FrontendContractLoadStatus;
  error: Error | null;
}

interface WorkspaceBrandProviderProps {
  workspaceId: string | null;
  brand?: Brand | null;
  loadBrand?: (scope: WorkspaceBrandScope) => Promise<Brand>;
  loadingFallback?: ReactNode;
  errorFallback?: ReactNode | ((error: Error) => ReactNode);
  children: ReactNode;
}

const BRAND_LOAD_TIMEOUT_MS = 8_000;
const BRAND_LOAD_ATTEMPTS = 2;

function boundedBrandLoad(load: () => Promise<Brand>): Promise<Brand> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      reject(new Error('A identidade visual não respondeu dentro do tempo esperado.'));
    }, BRAND_LOAD_TIMEOUT_MS);
    void load().then(
      (brand) => {
        window.clearTimeout(timeout);
        resolve(brand);
      },
      (error) => {
        window.clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

async function loadBrandWithFiniteRetry(load: () => Promise<Brand>): Promise<Brand> {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= BRAND_LOAD_ATTEMPTS; attempt += 1) {
    try {
      return await boundedBrandLoad(load);
    } catch (error) {
      lastError = error;
      if (attempt < BRAND_LOAD_ATTEMPTS) {
        await new Promise((resolve) => window.setTimeout(resolve, 150));
      }
    }
  }
  throw lastError ?? new Error('Falha desconhecida ao carregar a identidade visual.');
}

interface BrandState {
  workspaceId: string | null;
  status: FrontendContractLoadStatus;
  brand: Brand | null;
  error: Error | null;
}

const WorkspaceBrandContext = createContext<WorkspaceBrandContextValue | undefined>(undefined);

function configurationError(workspaceId: string): FrontendContractConfigurationError {
  return new FrontendContractConfigurationError({
    code: 'missing_contract',
    contractName: 'Brand',
    scopeKey: workspaceId,
    message: `Nenhuma fonte de Brand foi configurada para o workspace ${workspaceId}.`,
  });
}

function assertScope(brand: Brand, workspaceId: string): Brand {
  if (brand.workspaceId !== workspaceId) {
    throw new FrontendContractConfigurationError({
      code: 'scope_mismatch',
      contractName: 'Brand',
      scopeKey: workspaceId,
      message: `Brand pertence ao workspace ${brand.workspaceId}, não ao workspace ativo ${workspaceId}.`,
    });
  }
  return brand;
}

function recoverableBrandPersistenceError(error: unknown): boolean {
  if (error instanceof FrontendContractConfigurationError) {
    return error.code === 'persistence_unavailable';
  }
  if (typeof error === 'object' && error !== null && 'code' in error) {
    return error.code === 'persistence_unavailable';
  }
  return error instanceof Error
    && (
      error.name === 'TimeoutError'
      || error.message === 'A identidade visual não respondeu dentro do tempo esperado.'
    );
}

function defaultLoadingFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground" role="status">
      Carregando identidade do workspace...
    </div>
  );
}

function defaultErrorFallback(error: Error) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6 text-center" role="alert">
      <div className="max-w-xl border border-destructive/30 bg-card p-5 text-sm text-destructive">
        Configuração de marca indisponível. {error.message}
      </div>
    </div>
  );
}

export function WorkspaceBrandProvider({
  workspaceId,
  brand: providedBrand,
  loadBrand,
  loadingFallback,
  errorFallback,
  children,
}: WorkspaceBrandProviderProps) {
  const [state, setState] = useState<BrandState>({
    workspaceId,
    status: workspaceId ? 'loading' : 'idle',
    brand: null,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    if (!workspaceId) {
      setState({ workspaceId: null, status: 'idle', brand: null, error: null });
      return () => {
        cancelled = true;
      };
    }

    setState({ workspaceId, status: 'loading', brand: null, error: null });
    const resolveBrand = async () => {
      try {
        const resolved = providedBrand ?? await (loadBrand
          ? loadBrandWithFiniteRetry(() => loadBrand({ workspaceId }))
          : Promise.reject(configurationError(workspaceId)));
        if (!cancelled) {
          setState({
            workspaceId,
            status: 'ready',
            brand: assertScope(resolved, workspaceId),
            error: null,
          });
        }
      } catch (error) {
        if (!cancelled) {
          if (recoverableBrandPersistenceError(error)) {
            const persistenceError = error instanceof Error
              ? error
              : new Error(
                typeof error === 'object' && error !== null && 'message' in error
                  ? String(error.message)
                  : 'Identidade visual temporariamente indisponível.',
              );
            setState({
              workspaceId,
              status: 'ready',
              brand: buildDefaultBrand({ workspaceId }),
              error: persistenceError,
            });
            return;
          }
          setState({
            workspaceId,
            status: 'error',
            brand: null,
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      }
    };
    void resolveBrand();

    return () => {
      cancelled = true;
    };
  }, [loadBrand, providedBrand, workspaceId]);

  const stateForScope = state.workspaceId === workspaceId
    ? state
    : { workspaceId, status: workspaceId ? 'loading' as const : 'idle' as const, brand: null, error: null };

  useEffect(() => {
    if (stateForScope.status !== 'ready' || !stateForScope.brand) return undefined;
    return applyWorkspaceBrandToDocument(stateForScope.brand);
  }, [stateForScope.brand, stateForScope.status]);

  const value = useMemo<WorkspaceBrandContextValue>(() => ({
    scope: workspaceId ? { workspaceId } : null,
    brand: stateForScope.brand,
    status: stateForScope.status,
    error: stateForScope.error,
  }), [stateForScope.brand, stateForScope.error, stateForScope.status, workspaceId]);

  let content = children;
  if (stateForScope.status === 'loading') {
    content = loadingFallback ?? defaultLoadingFallback();
  } else if (stateForScope.status === 'error' && stateForScope.error) {
    content = typeof errorFallback === 'function'
      ? errorFallback(stateForScope.error)
      : errorFallback ?? defaultErrorFallback(stateForScope.error);
  }

  return (
    <WorkspaceBrandContext.Provider value={value}>
      {content}
    </WorkspaceBrandContext.Provider>
  );
}

export function useWorkspaceBrand(): WorkspaceBrandContextValue {
  const context = useContext(WorkspaceBrandContext);
  if (!context) throw new Error('useWorkspaceBrand deve ser usado dentro de WorkspaceBrandProvider.');
  return context;
}

export function useOptionalWorkspaceBrand(): WorkspaceBrandContextValue | null {
  return useContext(WorkspaceBrandContext) ?? null;
}
