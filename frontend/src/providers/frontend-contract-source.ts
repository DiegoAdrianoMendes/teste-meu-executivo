import type {
  AgentEffectivePolicy,
  AreaDefinition,
  Brand,
  PlaybookVersion,
  ProposalVersion,
  UserPreferences,
} from '@/types/frontend-contracts';
import {
  buildDefaultBrand,
  buildDefaultUserPreferences,
} from '@/branding/workspace-defaults';
import { getToken } from '@/services/auth';
import { apiFetch } from '@/services/http';

export interface WorkspaceBrandScope {
  workspaceId: string;
}

export interface UserPreferencesScope extends WorkspaceBrandScope {
  userId: string;
}

export interface FrontendContractRegistry {
  brandsByWorkspace: Record<string, Brand>;
  userPreferencesByScope: Record<string, UserPreferences>;
  proposalVersions?: ProposalVersion[];
  agentEffectivePolicies?: AgentEffectivePolicy[];
  areaDefinitions?: AreaDefinition[];
  playbookVersions?: PlaybookVersion[];
}

export type FrontendContractEnvironment = 'development' | 'test' | 'production';
export type FrontendContractName = 'Brand' | 'UserPreferences';

export class FrontendContractConfigurationError extends Error {
  readonly code: 'missing_contract' | 'scope_mismatch' | 'invalid_contract' | 'persistence_unavailable';
  readonly contractName: FrontendContractName;
  readonly scopeKey: string;

  constructor(options: {
    code: FrontendContractConfigurationError['code'];
    contractName: FrontendContractName;
    scopeKey: string;
    message: string;
    cause?: unknown;
  }) {
    super(options.message);
    if (options.cause !== undefined) {
      (this as Error & { cause?: unknown }).cause = options.cause;
    }
    this.name = 'FrontendContractConfigurationError';
    this.code = options.code;
    this.contractName = options.contractName;
    this.scopeKey = options.scopeKey;
  }
}

export interface FrontendContractSource {
  environment: FrontendContractEnvironment;
  loadBrand: (scope: WorkspaceBrandScope) => Promise<Brand>;
  loadUserPreferences: (scope: UserPreferencesScope) => Promise<UserPreferences>;
  saveUserPreferences: (scope: UserPreferencesScope, value: UserPreferences) => Promise<void>;
}

interface RuntimeEnvironment {
  DEV?: boolean;
  MODE?: string;
  PROD?: boolean;
}

interface FrontendContractSourceOptions {
  environment?: FrontendContractEnvironment;
  registry?: FrontendContractRegistry | null;
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null;
  request?: <T>(path: string, init?: RequestInit) => Promise<T>;
  preferApi?: boolean;
  /**
   * Indica se há sessão autenticada capaz de chamar a API de configurações.
   * Sem sessão, brand e preferências caem no padrão do produto (fail-open):
   * a identidade visual nunca pode bloquear a tela de login.
   */
  hasActiveSession?: () => boolean;
}

const REGISTRY_GLOBAL_KEY = '__KLM_FRONTEND_CONTRACTS__';
const USER_PREFERENCES_STORAGE_PREFIX = 'klm:user-preferences';
const CONTRACT_TIMESTAMP_FALLBACK = '1970-01-01T00:00:00.000Z';
const BRAND_REQUEST_TIMEOUT_MS = 7_000;
const USER_PREFERENCES_REQUEST_TIMEOUT_MS = 7_000;

interface ResolvedConfigurationResponse {
  namespace: string;
  effective: Record<string, unknown>;
  etag: string;
  layers: Array<{ scope: string; scopeKey: string; version: number; etag: string }>;
}

interface UserPreferencesResponse {
  preferences: Record<string, unknown>;
  etag: string;
  userEtag?: string | null;
  userVersion?: number | null;
  layers: Array<{ scope: string; scopeKey: string; version: number; etag: string }>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function recordValue(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function stringRecord(value: unknown): Record<`--${string}`, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [`--${string}`, string] => entry[0].startsWith('--') && typeof entry[1] === 'string',
    ),
  );
}

function maxLayerVersion(layers: Array<{ version: number }>): number {
  return Math.max(1, ...layers.map((layer) => layer.version));
}

function mapBrandResponse(
  response: ResolvedConfigurationResponse,
  scope: WorkspaceBrandScope,
): Brand {
  const value = recordValue(response.effective);
  const assets = recordValue(value.assets);
  const colors = recordValue(value.colors);
  const typography = recordValue(value.typography);
  const metadata = recordValue(value.metadata);
  const contact = recordValue(value.contact);
  const legal = recordValue(value.legal);
  const primary = stringValue(colors.primary ?? value.primaryColor, '#0057D8');
  const accent = stringValue(colors.accent ?? value.accentColor, '#1C3254');
  const name = stringValue(value.name ?? value.brandName, 'Meu Executivo');
  const logo = stringValue(assets.logoLightUrl ?? value.logoUrl, '/logo.svg');
  const mark = stringValue(assets.markUrl ?? value.iconUrl, '/logo-icon.png');
  const configuredVariables = stringRecord(value.cssVariables);

  return assertBrandScope({
    id: `brand:${scope.workspaceId}:${response.etag}`,
    workspaceId: scope.workspaceId,
    version: maxLayerVersion(response.layers),
    name,
    shortName: stringValue(value.shortName, 'Meu Executivo'),
    tagline: stringValue(value.tagline, 'Operação jurídica inteligente'),
    legalName: stringValue(value.legalName, 'KLM Advogados'),
    domain: stringValue(value.domain, 'klmadvogados.com'),
    defaultLocale: stringValue(value.defaultLocale, 'pt-BR'),
    assets: {
      logoLightUrl: logo,
      logoDarkUrl: stringValue(assets.logoDarkUrl, logo),
      markUrl: mark,
      faviconUrl: stringValue(assets.faviconUrl, mark),
      appleTouchIconUrl: typeof assets.appleTouchIconUrl === 'string' ? assets.appleTouchIconUrl : mark,
      letterheadUrl: typeof assets.letterheadUrl === 'string' ? assets.letterheadUrl : null,
      signatureUrl: typeof assets.signatureUrl === 'string' ? assets.signatureUrl : null,
    },
    colors: {
      primary,
      accent,
      foreground: stringValue(colors.foreground, '#131720'),
      background: stringValue(colors.background, '#FAFAF9'),
      sidebar: stringValue(colors.sidebar, '#050505'),
      success: stringValue(colors.success, '#237A3B'),
      warning: stringValue(colors.warning, '#D98E04'),
      danger: stringValue(colors.danger, '#B94A48'),
    },
    cssVariables: {
      '--primary': '39 67% 58%',
      '--accent': '37 56% 50%',
      '--ring': '39 67% 58%',
      '--sidebar-background': '0 0% 2%',
      '--sidebar-primary': '39 67% 58%',
      '--brand-gradient': 'hsl(0 0% 2%)',
      '--brand-gradient-subtle': 'hsl(39 67% 58% / 0.1)',
      '--ai-gradient': 'hsl(39 67% 58%)',
      '--brand-font-family': "'Geist Variable', 'Geist', sans-serif",
      ...configuredVariables,
    },
    typography: {
      sansFamily: stringValue(typography.sansFamily, "'Geist Variable', 'Geist', sans-serif"),
      legalFamily: stringValue(typography.legalFamily, "'Geist Variable', 'Geist', sans-serif"),
    },
    metadata: {
      defaultTitle: stringValue(metadata.defaultTitle, `${name} | Operação jurídica inteligente`),
      titleTemplate: stringValue(metadata.titleTemplate, `%s | ${name}`),
      description: stringValue(metadata.description, 'Atendimento, CRM, operação jurídica e inteligência comercial.'),
      applicationName: stringValue(metadata.applicationName, name),
      author: stringValue(metadata.author, 'KLM Advogados'),
      themeColor: stringValue(metadata.themeColor, '#050505'),
    },
    contact: {
      email: stringValue(contact.email, 'contato@klmadvogados.com'),
      supportEmail: stringValue(contact.supportEmail, 'contato@klmadvogados.com'),
      phone: typeof contact.phone === 'string' ? contact.phone : null,
      websiteUrl: stringValue(contact.websiteUrl, 'https://klmadvogados.com'),
    },
    legal: {
      registeredName: stringValue(legal.registeredName, 'KLM Advogados'),
      registrationNumber: typeof legal.registrationNumber === 'string' ? legal.registrationNumber : null,
      barRegistration: typeof legal.barRegistration === 'string' ? legal.barRegistration : null,
      address: typeof legal.address === 'string' ? legal.address : null,
    },
    socialProof: Array.isArray(value.socialProof) ? value.socialProof as Brand['socialProof'] : [],
    updatedAt: stringValue(value.updatedAt, CONTRACT_TIMESTAMP_FALLBACK),
    updatedBy: stringValue(value.updatedBy, 'system'),
  }, scope);
}

function mapUserPreferencesResponse(
  response: UserPreferencesResponse,
  scope: UserPreferencesScope,
): UserPreferences {
  const value = recordValue(response.preferences);
  const notifications = recordValue(value.notifications);
  const dailyDigest = recordValue(value.dailyDigest);
  const accessibility = recordValue(value.accessibility);
  const density = ['compact', 'comfortable', 'spacious'].includes(String(value.density))
    ? value.density as UserPreferences['density']
    : 'comfortable';
  const theme = ['light', 'dark', 'system'].includes(String(value.theme))
    ? value.theme as UserPreferences['theme']
    : 'system';

  return assertUserPreferencesScope({
    id: `preferences:${scope.workspaceId}:${scope.userId}`,
    workspaceId: scope.workspaceId,
    userId: scope.userId,
    version: response.userVersion ?? maxLayerVersion(response.layers),
    locale: stringValue(value.locale, 'pt-BR'),
    timezone: stringValue(value.timezone, 'America/Sao_Paulo'),
    currency: stringValue(value.currency, 'BRL'),
    dateFormat: stringValue(value.dateFormat, 'dd/MM/yyyy'),
    timeFormat: value.timeFormat === '12h' ? '12h' : '24h',
    theme,
    density,
    homePath: stringValue(value.homePath, '/app'),
    defaultFilters: recordValue(value.defaultFilters),
    notifications: {
      inApp: booleanValue(notifications.inApp, true),
      email: booleanValue(notifications.email, true),
      whatsapp: booleanValue(notifications.whatsapp, false),
      approvalRequests: booleanValue(notifications.approvalRequests, true),
      slaWarnings: booleanValue(notifications.slaWarnings, true),
    },
    dailyDigest: {
      enabled: booleanValue(dailyDigest.enabled, true),
      hour: numberValue(dailyDigest.hour, 8),
    },
    accessibility: {
      reducedMotion: booleanValue(accessibility.reducedMotion ?? value.reducedMotion, false),
      highContrast: booleanValue(accessibility.highContrast ?? value.highContrast, false),
      fontScale: numberValue(accessibility.fontScale, 1),
    },
    updatedAt: stringValue(value.updatedAt, CONTRACT_TIMESTAMP_FALLBACK),
  }, scope);
}

function preferencesWritePayload(value: UserPreferences): Record<string, unknown> {
  return {
    theme: value.theme,
    density: value.density,
    sidebarCollapsed: false,
    reducedMotion: value.accessibility.reducedMotion,
    highContrast: value.accessibility.highContrast,
    locale: value.locale,
    timezone: value.timezone,
    currency: value.currency,
    dateFormat: value.dateFormat,
    timeFormat: value.timeFormat,
    homePath: value.homePath,
    defaultFilters: value.defaultFilters,
    notifications: value.notifications,
    dailyDigest: value.dailyDigest,
    accessibility: value.accessibility,
  };
}

export function resolveFrontendContractEnvironment(
  runtime: RuntimeEnvironment = import.meta.env,
): FrontendContractEnvironment {
  if (runtime.MODE === 'test') return 'test';
  if (runtime.PROD) return 'production';
  return runtime.DEV ? 'development' : 'production';
}

export function userPreferencesScopeKey(scope: UserPreferencesScope): string {
  return `${scope.workspaceId}:${scope.userId}`;
}

export function userPreferencesStorageKey(scope: UserPreferencesScope): string {
  return `${USER_PREFERENCES_STORAGE_PREFIX}:${encodeURIComponent(scope.workspaceId)}:${encodeURIComponent(scope.userId)}`;
}

function readGlobalRegistry(): FrontendContractRegistry | null {
  const runtimeGlobal = globalThis as typeof globalThis & {
    [REGISTRY_GLOBAL_KEY]?: FrontendContractRegistry;
  };
  return runtimeGlobal[REGISTRY_GLOBAL_KEY] ?? null;
}

function browserStorage(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function assertBrandScope(brand: Brand, scope: WorkspaceBrandScope): Brand {
  if (
    !brand
    || typeof brand.id !== 'string'
    || typeof brand.name !== 'string'
    || typeof brand.metadata?.defaultTitle !== 'string'
    || typeof brand.metadata?.description !== 'string'
    || typeof brand.assets?.faviconUrl !== 'string'
    || !brand.cssVariables
  ) {
    throw new FrontendContractConfigurationError({
      code: 'invalid_contract',
      contractName: 'Brand',
      scopeKey: scope.workspaceId,
      message: `Brand configurada para o workspace ${scope.workspaceId} é inválida.`,
    });
  }
  if (brand.workspaceId !== scope.workspaceId) {
    throw new FrontendContractConfigurationError({
      code: 'scope_mismatch',
      contractName: 'Brand',
      scopeKey: scope.workspaceId,
      message: `Brand pertence ao workspace ${brand.workspaceId}, não ao workspace ativo ${scope.workspaceId}.`,
    });
  }
  return brand;
}

function assertUserPreferencesScope(
  preferences: UserPreferences,
  scope: UserPreferencesScope,
): UserPreferences {
  if (
    !preferences
    || typeof preferences.id !== 'string'
    || typeof preferences.locale !== 'string'
    || !['light', 'dark', 'system'].includes(preferences.theme)
    || !['compact', 'comfortable', 'spacious'].includes(preferences.density)
    || typeof preferences.accessibility?.fontScale !== 'number'
  ) {
    throw new FrontendContractConfigurationError({
      code: 'invalid_contract',
      contractName: 'UserPreferences',
      scopeKey: userPreferencesScopeKey(scope),
      message: 'UserPreferences configurada para o escopo ativo é inválida.',
    });
  }
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

function readStoredPreferences(
  storage: Pick<Storage, 'getItem'> | null,
  scope: UserPreferencesScope,
  strict: boolean,
): UserPreferences | null {
  if (!storage) return null;
  const raw = storage.getItem(userPreferencesStorageKey(scope));
  if (!raw) return null;
  try {
    return assertUserPreferencesScope(JSON.parse(raw) as UserPreferences, scope);
  } catch (error) {
    if (strict) {
      throw new FrontendContractConfigurationError({
        code: 'invalid_contract',
        contractName: 'UserPreferences',
        scopeKey: userPreferencesScopeKey(scope),
        message: 'As preferências persistidas para o escopo ativo são inválidas.',
        cause: error,
      });
    }
    return null;
  }
}

export function createFrontendContractSource(
  options: FrontendContractSourceOptions = {},
): FrontendContractSource {
  const environment = options.environment ?? resolveFrontendContractEnvironment();
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const getRegistry = () => options.registry === undefined ? readGlobalRegistry() : options.registry;
  const fixturesAllowed = environment === 'development' || environment === 'test';
  const runtimeAllowsFixtures = import.meta.env.DEV || import.meta.env.MODE === 'test';
  const request = options.request ?? apiFetch;
  const preferApi = options.preferApi ?? (
    environment === 'production'
    || import.meta.env.VITE_KLM_RUNTIME_MODE === 'connected_local'
    || import.meta.env.VITE_KLM_RUNTIME_MODE === 'local_simulation'
  );
  const hasActiveSession = options.hasActiveSession ?? (() => Boolean(getToken()));
  const preferenceEtags = new Map<string, string>();

  return {
    environment,
    async loadBrand(scope) {
      const registered = getRegistry()?.brandsByWorkspace?.[scope.workspaceId];
      if (registered) return assertBrandScope(registered, scope);

      // Sem sessão não há como consultar a API de configurações (401 certo):
      // branding é fail-open e o login carrega a identidade padrão do produto.
      if (preferApi && hasActiveSession()) {
        const controller = new AbortController();
        let response: ResolvedConfigurationResponse;
        const timeout = window.setTimeout(() => {
          controller.abort(new DOMException('Tempo limite ao carregar identidade visual.', 'TimeoutError'));
        }, BRAND_REQUEST_TIMEOUT_MS);
        try {
          response = await request<ResolvedConfigurationResponse>(
            '/api/v1/configurations/brand.identity/effective',
            { signal: controller.signal },
          );
        } catch (error) {
          throw new FrontendContractConfigurationError({
            code: 'persistence_unavailable',
            contractName: 'Brand',
            scopeKey: scope.workspaceId,
            message: `Não foi possível carregar a identidade do workspace ${scope.workspaceId}.`,
            cause: error,
          });
        } finally {
          window.clearTimeout(timeout);
        }
        // A resposta persistida só é validada depois do bloco de transporte.
        // Assim, contrato inválido ou de outro workspace não é reclassificado
        // como indisponibilidade recuperável.
        return mapBrandResponse(response, scope);
      }

      if (fixturesAllowed && runtimeAllowsFixtures) {
        const fixtures = await import('@klm/frontend-contract-fixtures');
        return fixtures.loadDevTestFrontendContractFixtures({
          workspaceId: scope.workspaceId,
          userId: fixtures.DEV_TEST_FRONTEND_SCOPE.userId,
        }).brand;
      }

      if (preferApi) {
        return buildDefaultBrand(scope);
      }

      throw new FrontendContractConfigurationError({
        code: 'missing_contract',
        contractName: 'Brand',
        scopeKey: scope.workspaceId,
        message: `Brand não foi configurada para o workspace ${scope.workspaceId}.`,
      });
    },
    async loadUserPreferences(scope) {
      const scopeKey = userPreferencesScopeKey(scope);
      const registered = getRegistry()?.userPreferencesByScope?.[scopeKey];
      if (registered) {
        // The API/registry is the canonical source. A stale browser value from
        // another session or an older schema must never take the application
        // down; it is ignored and the exact registered scope is validated.
        const stored = readStoredPreferences(storage, scope, false);
        if (stored) return stored;
        return assertUserPreferencesScope(registered, scope);
      }
      if (!preferApi && fixturesAllowed && runtimeAllowsFixtures) {
        const stored = readStoredPreferences(storage, scope, false);
        if (stored) return stored;
        const fixtures = await import('@klm/frontend-contract-fixtures');
        return fixtures.loadDevTestFrontendContractFixtures(scope).userPreferences;
      }

      if (preferApi && hasActiveSession()) {
        const controller = new AbortController();
        let response: UserPreferencesResponse;
        const timeout = window.setTimeout(() => {
          controller.abort(new DOMException('Tempo limite ao carregar preferências.', 'TimeoutError'));
        }, USER_PREFERENCES_REQUEST_TIMEOUT_MS);
        try {
          response = await request<UserPreferencesResponse>('/api/v1/users/me/preferences', {
            signal: controller.signal,
          });
        } catch (error) {
          // Preferências pessoais são um aprimoramento visual, não uma
          // condição de autorização. Uma indisponibilidade transitória da
          // persistência não pode desmontar toda a aplicação; o escopo ativo
          // continua explícito e a API seguirá sendo consultada novamente em
          // uma nova navegação/recarregamento. Respostas recebidas ainda são
          // validadas abaixo e continuam falhando fechado em caso de payload
          // inválido ou incompatibilidade de workspace/usuário.
          void error;
          return buildDefaultUserPreferences(scope);
        } finally {
          window.clearTimeout(timeout);
        }
        if (response.userEtag) preferenceEtags.set(scopeKey, response.userEtag);
        return mapUserPreferencesResponse(response, scope);
      }

      // Sem sessão: preferências padrão do produto (fail-open, como o brand).
      if (preferApi) {
        const stored = readStoredPreferences(storage, scope, environment === 'production');
        if (stored) return stored;
        if (fixturesAllowed && runtimeAllowsFixtures) {
          const fixtures = await import('@klm/frontend-contract-fixtures');
          return fixtures.loadDevTestFrontendContractFixtures(scope).userPreferences;
        }
        return buildDefaultUserPreferences(scope);
      }

      throw new FrontendContractConfigurationError({
        code: 'missing_contract',
        contractName: 'UserPreferences',
        scopeKey,
        message: `UserPreferences não foi configurada para o escopo ${scopeKey}.`,
      });
    },
    async saveUserPreferences(scope, value) {
      assertUserPreferencesScope(value, scope);
      const scopeKey = userPreferencesScopeKey(scope);
      if (preferApi) {
        try {
          const etag = preferenceEtags.get(scopeKey);
          const response = await request<UserPreferencesResponse>('/api/v1/users/me/preferences', {
            method: 'PUT',
            headers: etag ? { 'If-Match': `"${etag}"` } : undefined,
            body: JSON.stringify(preferencesWritePayload(value)),
          });
          if (response.userEtag) preferenceEtags.set(scopeKey, response.userEtag);
          return;
        } catch (error) {
          throw new FrontendContractConfigurationError({
            code: 'persistence_unavailable',
            contractName: 'UserPreferences',
            scopeKey,
            message: 'Não foi possível persistir as preferências no workspace ativo.',
            cause: error,
          });
        }
      }
      if (!storage) {
        throw new FrontendContractConfigurationError({
          code: 'persistence_unavailable',
          contractName: 'UserPreferences',
          scopeKey,
          message: 'O armazenamento de preferências não está disponível neste navegador.',
        });
      }
      storage.setItem(userPreferencesStorageKey(scope), JSON.stringify(value));
    },
  };
}

export const frontendContractSource = createFrontendContractSource();
