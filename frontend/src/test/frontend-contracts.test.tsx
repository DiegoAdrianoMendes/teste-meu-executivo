/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import {
  assertDevTestFixtureRuntime,
  createDevTestFrontendContractFixtures,
  loadDevTestFrontendContractFixtures,
} from '@/fixtures/dev-test/frontend-contracts';
import {
  createFrontendContractSource,
  FrontendContractConfigurationError,
  userPreferencesScopeKey,
} from '@/providers/frontend-contract-source';
import {
  useUserPreferences,
  UserPreferencesProvider,
} from '@/providers/user-preferences-provider';
import {
  useWorkspaceBrand,
  WorkspaceBrandProvider,
} from '@/providers/workspace-brand-provider';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('frontend-first dev/test fixtures', () => {
  it('keeps all six contracts deterministic and in the requested tenant scope', () => {
    const scope = { workspaceId: 'workspace-a', userId: 'user-a' };
    const fixtures = loadDevTestFrontendContractFixtures(scope, { DEV: false, MODE: 'test' });

    expect(fixtures.fixtureKind).toBe('dev-test');
    expect(fixtures.brand.workspaceId).toBe(scope.workspaceId);
    expect(fixtures.userPreferences).toMatchObject(scope);
    expect(fixtures.proposalVersion.workspaceId).toBe(scope.workspaceId);
    expect(fixtures.agentEffectivePolicy.workspaceId).toBe(scope.workspaceId);
    expect(fixtures.areaDefinition.workspaceId).toBe(scope.workspaceId);
    expect(fixtures.playbookVersion.workspaceId).toBe(scope.workspaceId);
    expect(fixtures.proposalVersion.playbookVersionId).toBe(fixtures.playbookVersion.id);
    expect(fixtures.agentEffectivePolicy.areaDefinitionId).toBe(fixtures.areaDefinition.id);
    expect(createDevTestFrontendContractFixtures(scope)).toEqual(fixtures);
  });

  it('rejects fixture loading outside development and test', () => {
    expect(() => assertDevTestFixtureRuntime({ DEV: false, MODE: 'production' }))
      .toThrow('exclusivas de desenvolvimento e teste');
    expect(() => createDevTestFrontendContractFixtures(
      { workspaceId: 'workspace-prod', userId: 'user-prod' },
      { DEV: false, MODE: 'production' },
    )).toThrow('exclusivas de desenvolvimento e teste');
  });
});

describe('production frontend contract source', () => {
  it('loads scoped brand and preferences from the authenticated KLM API', async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
      calls.push({ path, init });
      if (path.includes('brand.identity')) {
        return {
          namespace: 'brand.identity',
          effective: {
            name: 'KLM API',
            colors: { primary: '#DCA74C' },
            assets: { faviconUrl: '/api-favicon.svg' },
          },
          etag: 'brand-etag',
          layers: [{ scope: 'workspace', scopeKey: 'workspace:brand.identity', version: 4, etag: 'brand-etag' }],
        } as T;
      }
      const isWrite = init?.method === 'PUT';
      return {
        preferences: {
          theme: isWrite ? 'light' : 'dark',
          density: 'compact',
          locale: 'pt-BR',
          timezone: 'America/Sao_Paulo',
          accessibility: { reducedMotion: true, highContrast: false, fontScale: 1.1 },
        },
        etag: 'effective-etag',
        userEtag: isWrite ? 'user-etag-2' : 'user-etag-1',
        userVersion: isWrite ? 3 : 2,
        layers: [{ scope: 'user', scopeKey: 'user:user-prod:user.preferences', version: 2, etag: 'user-etag-1' }],
      } as T;
    };
    const source = createFrontendContractSource({
      environment: 'production',
      registry: null,
      storage: null,
      request,
      hasActiveSession: () => true,
    });

    const scope = { workspaceId: 'workspace-prod', userId: 'user-prod' };
    const brand = await source.loadBrand(scope);
    const preferences = await source.loadUserPreferences(scope);

    expect(brand).toMatchObject({ workspaceId: scope.workspaceId, name: 'KLM API', version: 4 });
    expect(preferences).toMatchObject({ ...scope, theme: 'dark', density: 'compact', version: 2 });
    await source.saveUserPreferences(scope, { ...preferences, theme: 'light', version: 3 });

    expect(calls[0]?.path).toBe('/api/v1/configurations/brand.identity/effective');
    expect(calls[1]?.path).toBe('/api/v1/users/me/preferences');
    expect(calls[0]?.init?.signal).toBeInstanceOf(AbortSignal);
    expect(calls[1]?.init?.signal).toBeInstanceOf(AbortSignal);
    expect(calls[2]?.init).toMatchObject({
      method: 'PUT',
      headers: { 'If-Match': '"user-etag-1"' },
    });
  });

  it('prefers the authenticated API over development fixtures in connected-local mode', async () => {
    const scope = { workspaceId: 'workspace-local', userId: 'user-local' };
    const calls: string[] = [];
    const request = async <T,>(path: string): Promise<T> => {
      calls.push(path);
      return {
        preferences: {
          theme: 'dark',
          density: 'compact',
          locale: 'pt-BR',
          timezone: 'America/Sao_Paulo',
          accessibility: { reducedMotion: false, highContrast: false, fontScale: 1 },
        },
        etag: 'effective-local',
        userEtag: 'user-local-etag',
        userVersion: 7,
        layers: [{ scope: 'user', scopeKey: 'user:user-local:user.preferences', version: 7, etag: 'user-local-etag' }],
      } as T;
    };
    const source = createFrontendContractSource({
      environment: 'development',
      preferApi: true,
      registry: null,
      storage: window.localStorage,
      request,
      hasActiveSession: () => true,
    });

    await expect(source.loadUserPreferences(scope)).resolves.toMatchObject({
      ...scope,
      theme: 'dark',
      version: 7,
    });
    expect(calls).toEqual(['/api/v1/users/me/preferences']);
  });

  it('fails closed when the production configuration API is unavailable', async () => {
    const source = createFrontendContractSource({
      environment: 'production',
      registry: null,
      storage: null,
      request: async () => { throw new Error('offline'); },
      hasActiveSession: () => true,
    });

    await expect(source.loadBrand({ workspaceId: 'workspace-prod' })).rejects.toMatchObject({
      name: 'FrontendContractConfigurationError',
      code: 'persistence_unavailable',
      contractName: 'Brand',
    });
  });

  it('keeps the authenticated application usable when preferences persistence is unavailable', async () => {
    const scope = { workspaceId: 'workspace-prod', userId: 'user-prod' };
    const source = createFrontendContractSource({
      environment: 'production',
      registry: null,
      storage: null,
      request: async () => { throw new Error('offline'); },
      hasActiveSession: () => true,
    });

    await expect(source.loadUserPreferences(scope)).resolves.toMatchObject({
      ...scope,
      theme: 'light',
      locale: 'pt-BR',
    });
  });

  it('falls back to the default product brand when there is no session (login screen)', async () => {
    const request = vi.fn(async () => { throw new Error('should not call the API without a session'); });
    const source = createFrontendContractSource({
      environment: 'production',
      registry: null,
      storage: null,
      request: request as never,
      hasActiveSession: () => false,
    });

    const brand = await source.loadBrand({ workspaceId: 'workspace-prod' });
    expect(brand).toMatchObject({ workspaceId: 'workspace-prod', name: 'Meu Executivo' });
    expect(request).not.toHaveBeenCalled();
  });

  it('falls back to default preferences when there is no session', async () => {
    const request = vi.fn(async () => { throw new Error('should not call the API without a session'); });
    const source = createFrontendContractSource({
      environment: 'production',
      registry: null,
      storage: null,
      request: request as never,
      hasActiveSession: () => false,
    });

    const scope = { workspaceId: 'workspace-prod', userId: 'user-prod' };
    const preferences = await source.loadUserPreferences(scope);
    expect(preferences).toMatchObject({ ...scope, theme: 'light', locale: 'pt-BR' });
    expect(request).not.toHaveBeenCalled();
  });

  it('reads only the exact injected user/workspace scope', async () => {
    const scope = { workspaceId: 'workspace-prod', userId: 'user-prod' };
    const fixtures = createDevTestFrontendContractFixtures(scope);
    const source = createFrontendContractSource({
      environment: 'production',
      registry: {
        brandsByWorkspace: { [scope.workspaceId]: fixtures.brand },
        userPreferencesByScope: { [userPreferencesScopeKey(scope)]: fixtures.userPreferences },
      },
      storage: null,
    });

    await expect(source.loadBrand(scope)).resolves.toEqual(fixtures.brand);
    await expect(source.loadUserPreferences(scope)).resolves.toEqual(fixtures.userPreferences);
    // Workspace não registrado nunca herda a marca de outro workspace: sem
    // sessão, resolve para a identidade padrão neutra escopada a ele mesmo.
    const otherBrand = await source.loadBrand({ workspaceId: 'workspace-other' });
    expect(otherBrand.workspaceId).toBe('workspace-other');
    expect(otherBrand).not.toEqual(fixtures.brand);
    expect(otherBrand.name).toBe('Meu Executivo');
  });
});

function BrandProbe() {
  const { brand, status, error } = useWorkspaceBrand();
  return <output>{status}:{brand?.name ?? 'none'}:{error?.message ?? 'healthy'}</output>;
}

describe('WorkspaceBrandProvider', () => {
  it('retries one idempotent brand read and then resolves without an infinite loading state', async () => {
    const fixtures = createDevTestFrontendContractFixtures({ workspaceId: 'workspace-retry', userId: 'user-a' });
    const loadBrand = vi.fn()
      .mockRejectedValueOnce(new Error('falha transitória'))
      .mockResolvedValueOnce(fixtures.brand);

    render(
      <WorkspaceBrandProvider workspaceId="workspace-retry" loadBrand={loadBrand}>
        <BrandProbe />
      </WorkspaceBrandProvider>,
    );

    expect(await screen.findByText(`ready:${fixtures.brand.name}:healthy`)).toBeInTheDocument();
    expect(loadBrand).toHaveBeenCalledTimes(2);
  });

  it('applies scoped CSS variables, metadata and favicons, then restores the document', async () => {
    const fixtures = createDevTestFrontendContractFixtures({ workspaceId: 'workspace-brand', userId: 'user-a' });
    const brand = {
      ...fixtures.brand,
      name: 'Marca Workspace A',
      cssVariables: { '--primary': '210 50% 30%', '--brand-test': 'workspace-a' },
      assets: { ...fixtures.brand.assets, faviconUrl: '/workspace-a.ico' },
      metadata: {
        ...fixtures.brand.metadata,
        defaultTitle: 'Workspace A | Hub',
        description: 'Descrição exclusiva do workspace A.',
      },
    };
    const originalTitle = document.title;
    const icon = document.createElement('link');
    icon.rel = 'icon';
    icon.href = '/original.ico';
    document.head.appendChild(icon);

    const { unmount } = render(
      <WorkspaceBrandProvider workspaceId="workspace-brand" brand={brand}>
        <BrandProbe />
      </WorkspaceBrandProvider>,
    );

    expect(await screen.findByText('ready:Marca Workspace A:healthy')).toBeInTheDocument();
    expect(document.documentElement.style.getPropertyValue('--brand-test')).toBe('workspace-a');
    expect(document.documentElement.dataset.brandWorkspaceId).toBe('workspace-brand');
    expect(document.title).toBe('Workspace A | Hub');
    expect(document.head.querySelector('meta[name="description"]')).toHaveAttribute(
      'content',
      'Descrição exclusiva do workspace A.',
    );
    expect(icon.getAttribute('href')).toBe('/workspace-a.ico');

    unmount();
    expect(document.documentElement.style.getPropertyValue('--brand-test')).toBe('');
    expect(document.documentElement).not.toHaveAttribute('data-brand-workspace-id');
    expect(document.title).toBe(originalTitle);
    expect(icon.getAttribute('href')).toBe('/original.ico');
    icon.remove();
  });

  it('blocks children and surfaces a missing source instead of using a fallback brand', async () => {
    render(
      <WorkspaceBrandProvider
        workspaceId="workspace-missing"
        errorFallback={(error) => <output>{error.message}</output>}
      >
        <div>conteúdo não deve renderizar</div>
      </WorkspaceBrandProvider>,
    );

    expect(await screen.findByText(/Nenhuma fonte de Brand/)).toBeInTheDocument();
    expect(screen.queryByText('conteúdo não deve renderizar')).not.toBeInTheDocument();
  });

  it('keeps blocking an invalid persisted brand contract', async () => {
    const loadBrand = vi.fn().mockRejectedValue(new FrontendContractConfigurationError({
      code: 'invalid_contract',
      contractName: 'Brand',
      scopeKey: 'workspace-invalid',
      message: 'Brand persistida inválida.',
    }));

    render(
      <WorkspaceBrandProvider
        workspaceId="workspace-invalid"
        loadBrand={loadBrand}
        errorFallback={(error) => <output>{error.message}</output>}
      >
        <div>conteúdo não deve renderizar</div>
      </WorkspaceBrandProvider>,
    );

    expect(await screen.findByText('Brand persistida inválida.')).toBeInTheDocument();
    expect(screen.queryByText('conteúdo não deve renderizar')).not.toBeInTheDocument();
  });

  it('keeps the application usable with the default brand when persistence is unavailable', async () => {
    const loadBrand = vi.fn().mockRejectedValue(new FrontendContractConfigurationError({
      code: 'persistence_unavailable',
      contractName: 'Brand',
      scopeKey: 'workspace-degraded',
      message: 'Configuração persistida indisponível.',
    }));

    render(
      <WorkspaceBrandProvider workspaceId="workspace-degraded" loadBrand={loadBrand}>
        <BrandProbe />
      </WorkspaceBrandProvider>,
    );

    expect(await screen.findByText(
      'ready:Meu Executivo:Configuração persistida indisponível.',
    )).toBeInTheDocument();
    expect(loadBrand).toHaveBeenCalledTimes(2);
  });

  it('recovers from a persistence error reconstructed across a bundle boundary', async () => {
    const loadBrand = vi.fn().mockRejectedValue({
      name: 'FrontendContractConfigurationError',
      code: 'persistence_unavailable',
      contractName: 'Brand',
      message: 'Identidade temporariamente indisponível.',
    });

    render(
      <WorkspaceBrandProvider workspaceId="workspace-cross-bundle" loadBrand={loadBrand}>
        <BrandProbe />
      </WorkspaceBrandProvider>,
    );

    expect(await screen.findByText(
      'ready:Meu Executivo:Identidade temporariamente indisponível.',
    )).toBeInTheDocument();
    expect(loadBrand).toHaveBeenCalledTimes(2);
  });
});

function PreferencesProbe() {
  const { preferences, resolvedTheme, updatePreferences } = useUserPreferences();
  return (
    <div>
      <output>{preferences?.workspaceId}:{preferences?.userId}:{resolvedTheme}</output>
      <button type="button" onClick={() => void updatePreferences({ theme: 'light' })}>Tema claro</button>
    </div>
  );
}

describe('UserPreferencesProvider', () => {
  it('retries one idempotent preferences read and then resolves without infinite loading', async () => {
    const scope = { workspaceId: 'workspace-preferences-retry', userId: 'user-preferences-retry' };
    const preferences = createDevTestFrontendContractFixtures(scope).userPreferences;
    const loadPreferences = vi.fn()
      .mockRejectedValueOnce(new Error('falha transitória'))
      .mockResolvedValueOnce(preferences);

    render(
      <UserPreferencesProvider
        workspaceId={scope.workspaceId}
        userId={scope.userId}
        loadPreferences={loadPreferences}
      >
        <PreferencesProbe />
      </UserPreferencesProvider>,
    );

    expect(await screen.findByText('workspace-preferences-retry:user-preferences-retry:light')).toBeInTheDocument();
    expect(loadPreferences).toHaveBeenCalledTimes(2);
  });

  it('keeps the application usable with default preferences when persistence is unavailable', async () => {
    const scope = { workspaceId: 'workspace-preferences-degraded', userId: 'user-preferences-degraded' };
    const loadPreferences = vi.fn().mockRejectedValue({
      name: 'FrontendContractConfigurationError',
      code: 'persistence_unavailable',
      contractName: 'UserPreferences',
      message: 'Preferências temporariamente indisponíveis.',
    });

    render(
      <UserPreferencesProvider
        workspaceId={scope.workspaceId}
        userId={scope.userId}
        loadPreferences={loadPreferences}
      >
        <PreferencesProbe />
      </UserPreferencesProvider>,
    );

    expect(await screen.findByText(
      'workspace-preferences-degraded:user-preferences-degraded:light',
    )).toBeInTheDocument();
    expect(loadPreferences).toHaveBeenCalledTimes(2);
  });

  it('discards preferences from another scope without blanking the application', async () => {
    const scope = { workspaceId: 'workspace-preferences-invalid', userId: 'user-preferences-invalid' };
    const loadPreferences = vi.fn().mockRejectedValue(new FrontendContractConfigurationError({
      code: 'scope_mismatch',
      contractName: 'UserPreferences',
      scopeKey: `${scope.workspaceId}:${scope.userId}`,
      message: 'Preferências pertencem a outro escopo.',
    }));

    render(
      <UserPreferencesProvider
        workspaceId={scope.workspaceId}
        userId={scope.userId}
        loadPreferences={loadPreferences}
      >
        <PreferencesProbe />
      </UserPreferencesProvider>,
    );

    expect(await screen.findByText(
      'workspace-preferences-invalid:user-preferences-invalid:light',
    )).toBeInTheDocument();
    expect(loadPreferences).toHaveBeenCalledTimes(2);
  });

  it('applies preferences and persists updates under the exact user/workspace scope', async () => {
    const scope = { workspaceId: 'workspace-preferences', userId: 'user-preferences' };
    const fixture = createDevTestFrontendContractFixtures(scope).userPreferences;
    const preferences = { ...fixture, theme: 'dark' as const, density: 'compact' as const };
    const savePreferences = vi.fn(async () => undefined);

    const { unmount } = render(
      <UserPreferencesProvider
        workspaceId={scope.workspaceId}
        userId={scope.userId}
        preferences={preferences}
        savePreferences={savePreferences}
      >
        <PreferencesProbe />
      </UserPreferencesProvider>,
    );

    expect(await screen.findByText('workspace-preferences:user-preferences:dark')).toBeInTheDocument();
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement).toHaveAttribute('data-density', 'compact');
    expect(document.documentElement).toHaveAttribute(
      'data-user-preferences-scope',
      'workspace-preferences:user-preferences',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Tema claro' }));
    await waitFor(() => expect(savePreferences).toHaveBeenCalledWith(
      scope,
      expect.objectContaining({
        workspaceId: scope.workspaceId,
        userId: scope.userId,
        theme: 'light',
        version: fixture.version + 1,
      }),
    ));
    await waitFor(() => expect(document.documentElement).not.toHaveClass('dark'));

    unmount();
    expect(document.documentElement).not.toHaveAttribute('data-user-preferences-scope');
  });
});
