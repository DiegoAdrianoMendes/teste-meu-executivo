import type { Brand, UserPreferences } from '@/types/frontend-contracts';

/**
 * Identidade padrão do produto (Meu Executivo).
 *
 * É a fonte única usada (a) pelas fixtures de dev/teste e (b) como fallback
 * fail-open de branding quando não há sessão autenticada — a identidade
 * visual nunca pode bloquear a aplicação (ver
 * docs/META_AUDITORIA_UI_2026-07-24.md). Após o login, a marca efetiva do
 * workspace substitui esta.
 */
export const DEFAULT_BRAND_TIMESTAMP = '2026-07-19T12:00:00.000Z';

export interface WorkspaceDefaultScope {
  workspaceId: string;
  userId: string;
}

export function buildDefaultBrand(
  scope: { workspaceId: string; userId?: string },
  updatedAt: string = DEFAULT_BRAND_TIMESTAMP,
): Brand {
  return {
    id: `brand:${scope.workspaceId}:1`,
    workspaceId: scope.workspaceId,
    version: 1,
    name: 'Meu Executivo',
    shortName: 'ME',
    tagline: 'Sistema operacional com IA',
    legalName: 'Meu Executivo',
    domain: 'meuexecutivo.com.br',
    defaultLocale: 'pt-BR',
    assets: {
      logoLightUrl: '/brand/meu-executivo/wordmark.svg',
      logoDarkUrl: '/brand/meu-executivo/wordmark-inverse.svg',
      markUrl: '/brand/meu-executivo/mark-blue.svg',
      faviconUrl: '/brand/meu-executivo/mark-blue.svg',
      appleTouchIconUrl: '/logo-icon.png',
      letterheadUrl: null,
      signatureUrl: null,
    },
    colors: {
      primary: '#0066F7',
      accent: '#1478FF',
      foreground: '#031121',
      background: '#F6F9FC',
      sidebar: '#031121',
      success: '#237A3B',
      warning: '#D98E04',
      danger: '#B94A48',
    },
    cssVariables: {
      '--primary': '215 100% 48%',
      '--accent': '213 100% 96%',
      '--ring': '215 100% 48%',
      '--sidebar-background': '211 83% 7%',
      '--sidebar-primary': '215 100% 48%',
      '--brand-gradient': 'linear-gradient(135deg, #0066f7 0%, #1478ff 100%)',
      '--brand-gradient-subtle': 'hsl(215 100% 48% / 0.08)',
      '--ai-gradient': 'linear-gradient(135deg, #0066f7 0%, #1478ff 100%)',
      '--brand-font-family': "'Lexend Variable', 'Lexend', sans-serif",
    },
    typography: {
      sansFamily: "'Lexend Variable', 'Lexend', sans-serif",
      legalFamily: "'Lexend Variable', 'Lexend', sans-serif",
    },
    metadata: {
      defaultTitle: 'Meu Executivo | Sistema operacional com IA',
      titleTemplate: '%s | Meu Executivo',
      description: 'Sistema operacional com IA para escritórios.',
      applicationName: 'Meu Executivo',
      author: 'Meu Executivo',
      themeColor: '#031121',
    },
    contact: {
      email: 'contato@klmadvogados.com',
      supportEmail: 'contato@klmadvogados.com',
      phone: null,
      websiteUrl: 'https://klmadvogados.com',
    },
    legal: {
      registeredName: 'KLM Advogados',
      registrationNumber: null,
      barRegistration: null,
      address: null,
    },
    socialProof: [],
    updatedAt,
    updatedBy: scope.userId ?? 'system-default',
  };
}

export function buildDefaultUserPreferences(
  scope: WorkspaceDefaultScope,
  updatedAt: string = DEFAULT_BRAND_TIMESTAMP,
): UserPreferences {
  return {
    id: `preferences:${scope.workspaceId}:${scope.userId}`,
    workspaceId: scope.workspaceId,
    userId: scope.userId,
    version: 1,
    locale: 'pt-BR',
    timezone: 'America/Sao_Paulo',
    currency: 'BRL',
    dateFormat: 'dd/MM/yyyy',
    timeFormat: '24h',
    theme: 'light',
    density: 'comfortable',
    homePath: '/app',
    defaultFilters: {},
    notifications: {
      inApp: true,
      email: true,
      whatsapp: false,
      approvalRequests: true,
      slaWarnings: true,
    },
    dailyDigest: {
      enabled: true,
      hour: 8,
    },
    accessibility: {
      reducedMotion: false,
      highContrast: false,
      fontScale: 1,
    },
    updatedAt,
  };
}
