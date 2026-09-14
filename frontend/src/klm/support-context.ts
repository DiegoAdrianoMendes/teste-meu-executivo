import type { KlmSupportTechnicalContext } from '@/klm/api';

const SAFE_TECHNICAL_ID = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,119}$/;
const MAX_FAILURE_IDS = 20;

function safeRoute(route: string): string {
  const path = route.split(/[?#]/, 1)[0]?.trim() || '/app';
  if (path !== '/app' && !path.startsWith('/app/')) return '/app';
  const segments = path.split('/').map((segment) => {
    if (!segment || segment === 'app') return segment;
    if (
      /^\d{4,}$/.test(segment)
      || /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(segment)
      || segment.includes('@')
    ) {
      return ':id';
    }
    return /^[A-Za-z0-9._~-]{1,64}$/.test(segment) ? segment : ':segment';
  });
  return segments.join('/').slice(0, 500);
}

function browserLabel(userAgent: string): string {
  const candidates: Array<[RegExp, string]> = [
    [/\bEdg\/(\d+)/, 'Edge'],
    [/\bOPR\/(\d+)/, 'Opera'],
    [/\bChrome\/(\d+)/, 'Chrome'],
    [/\bFirefox\/(\d+)/, 'Firefox'],
    [/\bVersion\/(\d+).*\bSafari\//, 'Safari'],
  ];
  for (const [pattern, label] of candidates) {
    const match = userAgent.match(pattern);
    if (match) return `${label} ${match[1]}`;
  }
  return 'Navegador não identificado';
}

function deviceLabel(userAgent: string, width: number): string {
  if (/\biPad\b|\bTablet\b/i.test(userAgent)) return 'Tablet';
  if (/\bMobi\b|\biPhone\b|\bAndroid\b/i.test(userAgent) || width < 768) return 'Celular';
  return 'Computador';
}

export function sanitizeSupportFailureIds(values: unknown[]): string[] {
  const unique = new Set<string>();
  for (const raw of values) {
    if (typeof raw !== 'string') continue;
    const value = raw.trim();
    if (SAFE_TECHNICAL_ID.test(value)) unique.add(value);
    if (unique.size >= MAX_FAILURE_IDS) break;
  }
  return [...unique];
}

export function collectSupportTechnicalContext(options: {
  route: string;
  realtimeStatus?: string | null;
  failureIds?: unknown[];
  now?: Date;
  navigatorLike?: Pick<Navigator, 'onLine' | 'userAgent'>;
  viewport?: { width: number; height: number; devicePixelRatio: number };
}): KlmSupportTechnicalContext {
  const navigatorLike = options.navigatorLike ?? navigator;
  const viewport = options.viewport ?? {
    width: window.screen.width || window.innerWidth,
    height: window.screen.height || window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
  };
  const realtimeStatus = options.realtimeStatus?.trim() ?? '';
  const connection = navigatorLike.onLine
    ? (/^[a-z_-]{1,40}$/i.test(realtimeStatus) ? realtimeStatus.toLowerCase() : 'online')
    : 'offline';
  const rawVersion = String(import.meta.env.VITE_APP_VERSION ?? '').trim();
  const version = /^[A-Za-z0-9._+-]{1,80}$/.test(rawVersion) ? rawVersion : null;

  return {
    page: safeRoute(options.route),
    action: 'support_center_submission',
    frontendVersion: version,
    browser: browserLabel(navigatorLike.userAgent),
    device: deviceLabel(navigatorLike.userAgent, viewport.width),
    viewport: `${Math.round(viewport.width)}x${Math.round(viewport.height)} @${viewport.devicePixelRatio}x`.slice(0, 40),
    occurredAt: (options.now ?? new Date()).toISOString(),
    realtimeStatus: connection,
    failedRequestIds: sanitizeSupportFailureIds(options.failureIds ?? []),
  };
}
